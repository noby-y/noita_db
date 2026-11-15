"""The filter compiler, checked by running its SQL against a real build."""

from __future__ import annotations

import pytest

from noita_tui import catalog as catalog_module
from noita_tui import db
from noita_tui.query import (Filter, FilterError, Scope, coerce, entity_query,
                             problem)


@pytest.fixture
def connection(built_database):
    connection = db.connect(built_database)
    yield connection
    connection.close()


@pytest.fixture
def catalog(connection):
    return catalog_module.load(connection)


def names(connection, filters, combinator='AND', scope=Scope(), catalog=None):
    sql, params = entity_query(filters, combinator, scope, catalog).select()
    return [row['name'] for row in connection.execute(sql, params)]


def test_no_filters_matches_everything(connection, catalog):
    assert len(names(connection, [], catalog=catalog)) == \
        connection.execute('SELECT count(*) FROM entities').fetchone()[0]


def test_attribute_predicate(connection, catalog):
    matched = names(connection, [Filter('ProjectileComponent', 'damage', '=', '0.12')],
                    catalog=catalog)
    assert matched == ['projectile_default']


def test_numeric_value_is_cast_for_a_numeric_column(connection, catalog):
    # Bound as '1', the comparison would be number against string and match
    # nothing: SQLite sorts every number before every text value.
    filters = [Filter('ProjectileComponent', 'damage', '<', '1')]
    assert entity_query(filters, catalog=catalog).params == (1,)
    assert names(connection, filters, catalog=catalog) == ['projectile_default']


def test_text_in_an_integer_column_stays_text(connection, catalog):
    # material is an INTEGER column holding 'wood_prop', because the engine
    # resolves the name on load.
    assert names(connection,
                 [Filter('PhysicsImageShapeComponent', 'material', '=', 'wood_prop')],
                 catalog=catalog) == ['projectile_default']


def test_has_component_needs_no_attribute(connection, catalog):
    assert names(connection, [Filter('MaterialInventoryComponent')],
                 catalog=catalog) == ['fuse']


def test_contains_matches_a_substring(connection, catalog):
    assert names(connection,
                 [Filter('LuaComponent', 'script_source_file', 'contains', 'replaced')],
                 catalog=catalog) == ['projectile_default']


def test_contains_escapes_wildcards(connection, catalog):
    assert names(connection,
                 [Filter('LuaComponent', 'script_source_file', 'contains', '%')],
                 catalog=catalog) == []


def test_two_predicates_on_one_binding_are_the_same_instance(connection, catalog):
    # base_projectile carries a.lua and b.lua on two separate components, so no
    # single instance holds both.
    same = [Filter('LuaComponent', 'script_source_file', 'contains', 'a.lua'),
            Filter('LuaComponent', 'script_source_file', 'contains', 'b.lua')]
    assert names(connection, same, catalog=catalog) == []


def test_two_bindings_are_different_instances(connection, catalog):
    apart = [Filter('LuaComponent', 'script_source_file', 'contains', 'a.lua', 1),
             Filter('LuaComponent', 'script_source_file', 'contains', 'b.lua', 2)]
    assert names(connection, apart, catalog=catalog) == ['base_projectile']
    query = entity_query(apart, catalog=catalog)
    assert 'b0.component_id <> b1.component_id' in query.where


def test_two_bindings_can_share_an_instance_only_under_or(connection, catalog):
    filters = [Filter('LuaComponent', 'script_source_file', 'contains', 'a.lua', 1),
               Filter('LuaComponent', 'script_source_file', 'contains', 'replaced', 2)]
    assert names(connection, filters, catalog=catalog) == []
    assert names(connection, filters, 'OR', catalog=catalog) == \
        ['base_projectile', 'projectile_default']


def test_xor_is_exactly_one_with_three_terms(connection, catalog):
    filters = [Filter('VelocityComponent', binding=1),
               Filter('ProjectileComponent', binding=1),
               Filter('LuaComponent', binding=1)]
    # base_projectile and light_bullet have all three, fuse only velocity.
    assert names(connection, filters, 'XOR', catalog=catalog) == ['fuse', 'homing']
    assert names(connection, filters, 'OR', catalog=catalog) == \
        ['base_projectile', 'fuse', 'homing', 'projectile_default']
    assert names(connection, filters, 'AND', catalog=catalog) == \
        ['base_projectile', 'projectile_default']


def test_scopes_are_anded_even_under_or(connection, catalog):
    filters = [Filter('VelocityComponent'), Filter('AreaDamageComponent')]
    scope = Scope(tag='projectile')
    assert names(connection, filters, 'OR', scope, catalog) == \
        ['base_projectile', 'projectile_default']


def test_text_scope_searches_name_display_name_and_file(connection, catalog):
    assert names(connection, [], scope=Scope(text='Base enemy'),
                 catalog=catalog) == ['base_enemy']
    assert names(connection, [], scope=Scope(text='particles'),
                 catalog=catalog) == ['spark']


def test_group_scope_restricts_to_members(connection, catalog):
    group_id = connection.execute(
        "INSERT INTO entity_groups (name) VALUES ('favourites')"
        ' RETURNING id').fetchone()[0]
    connection.execute(
        'INSERT INTO entity_group_members (group_id, entity_key) VALUES (?, ?)',
        (group_id, 'data/entities/projectiles/fuse.xml#'))
    assert names(connection, [], scope=Scope(group_id=group_id),
                 catalog=catalog) == ['fuse']


def test_unknown_identifiers_never_reach_the_sql(catalog):
    with pytest.raises(FilterError):
        entity_query([Filter('NoSuchComponent', 'damage', '=', '1')], catalog=catalog)
    with pytest.raises(FilterError):
        entity_query([Filter('ProjectileComponent', 'no_such_attribute', '=', '1')],
                     catalog=catalog)


def test_problem_describes_a_half_typed_row(catalog):
    assert problem(Filter(''), catalog) == 'no component type'
    assert problem(Filter('Projectile'), catalog).startswith('unknown component')
    assert problem(Filter('ProjectileComponent'), catalog) is None
    assert problem(Filter('ProjectileComponent', 'damage', '=', ''), catalog) == 'no value'
    assert problem(Filter('ProjectileComponent', 'damage', '=', '5'), catalog) is None
    # An empty field is a row still being typed; `""` is a question.
    assert problem(Filter('ProjectileComponent', 'damage', '=', '""'), catalog) is None


def test_a_quoted_empty_value_finds_an_attribute_holding_nothing(connection, catalog):
    # homing.xml has a bare <LuaComponent/>, so its script_source_file is the
    # documented default of ''. The two that name a script are not empty.
    empty = [Filter('LuaComponent', 'script_source_file', '=', '""')]
    assert names(connection, empty, catalog=catalog) == ['homing']
    assert names(connection, [Filter('LuaComponent', 'script_source_file', '!=', '""')],
                 catalog=catalog) == ['base_projectile', 'projectile_default']


def test_an_empty_value_also_matches_a_null_column(connection, catalog):
    # A container field with no rows leaves its column NULL rather than '' —
    # 18137 cells of the shipped data are that — and asking for empty means
    # asking for both. Nothing in the fixture is NULL, so the row is made here.
    spark = connection.execute(
        "SELECT id FROM entities WHERE name = 'spark'").fetchone()['id']
    connection.execute(
        'INSERT INTO LuaComponent (component_id, entity_id, instance_index,'
        ' script_source_file) VALUES (900, ?, 0, NULL)', (spark,))
    filters = [Filter('LuaComponent', 'script_source_file', '=', '""')]
    assert names(connection, filters, catalog=catalog) == ['homing', 'spark']


def test_quotes_also_pin_a_value_that_is_not_empty(connection, catalog):
    filters = [Filter('SpriteComponent', 'image_file', '=', '"data/spark.xml"')]
    assert names(connection, filters, catalog=catalog) == ['projectile_default']


def test_an_empty_value_is_only_meaningful_for_equality(catalog):
    item = Filter('LuaComponent', 'script_source_file', '<', '""')
    assert problem(item, catalog) == '< needs something to compare'
    with pytest.raises(FilterError):
        entity_query([item], catalog=catalog)


def test_a_quoted_column_survives_a_dotted_path(connection, catalog):
    filters = [Filter('ProjectileComponent', 'config_explosion.explosion_radius',
                      '>', '5')]
    assert names(connection, filters, catalog=catalog) == \
        ['base_projectile', 'projectile_default']


def test_coerce_leaves_a_material_name_alone():
    assert coerce('5', numeric=True) == 5
    assert coerce('5.5', numeric=True) == 5.5
    assert coerce('wood_prop', numeric=True) == 'wood_prop'
    assert coerce('5', numeric=False) == '5'


def test_query_count_matches_select(connection, catalog):
    query = entity_query([Filter('SpriteComponent')], catalog=catalog)
    sql, params = query.count()
    assert connection.execute(sql, params).fetchone()[0] == 2

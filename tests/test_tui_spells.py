"""The spell filter compiler, checked against a real build."""

from __future__ import annotations

import pytest

from noita_tui import catalog as catalog_module
from noita_tui import db, spell_detail
from noita_tui.query import FilterError
from noita_tui.spell_query import SpellFilter, SpellScope, problem, spell_query


@pytest.fixture
def connection(built_database):
    connection = db.connect(built_database)
    yield connection
    connection.close()


@pytest.fixture
def catalog(connection):
    return catalog_module.load_spells(connection)


def ids(connection, filters, combinator='AND', scope=SpellScope(), catalog=None):
    sql, params = spell_query(filters, combinator, scope, catalog).select(
        columns='s.action_id', order='s.action_id')
    return [row[0] for row in connection.execute(sql, params)]


def test_catalog_reads_the_columns_the_lua_actually_uses(catalog):
    names = [column.name for column in catalog.columns]
    assert 'mana' in names and 'spawn_probability' in names
    assert 'id' not in names
    assert catalog.spells == 3
    assert catalog.effect_scopes == ('c', 'global', 'shot_effects')
    assert 'screenshake' in catalog.effect_keys['c']
    assert catalog.sources[:2] == ['spell', 'c']
    assert 'projectile' in catalog.sources


def test_a_property_predicate(connection, catalog):
    assert ids(connection, [SpellFilter('spell', 'mana', '<=', '5')],
               catalog=catalog) == ['LIGHT_BULLET', 'TRIGGER']
    assert ids(connection, [SpellFilter('spell', 'type', '=', 'ACTION_TYPE_MODIFIER')],
               catalog=catalog) == ['HOMING', 'TRIGGER']


def test_a_blank_value_means_the_property_is_set(connection, catalog):
    # max_uses is NULL for the spells that have no limit.
    assert ids(connection, [SpellFilter('spell', 'max_uses')],
               catalog=catalog) == ['HOMING']


def test_an_effect_is_searchable_by_the_value_it_writes(connection, catalog):
    # c.screenshake = c.screenshake + 10.0 is stored as add 10.0.
    assert ids(connection, [SpellFilter('c', 'screenshake', '=', '10')],
               catalog=catalog) == ['LIGHT_BULLET']
    assert ids(connection, [SpellFilter('c', 'screenshake', '>=', '10')],
               catalog=catalog) == ['LIGHT_BULLET']
    assert ids(connection, [SpellFilter('c', 'screenshake', '>', '10')],
               catalog=catalog) == []


def test_a_negative_value_finds_the_writes_that_lower_a_key(connection, catalog):
    # There is no `sub`: a subtraction is an add of a negative number.
    assert ids(connection, [SpellFilter('c', 'fire_rate_wait', '<', '0')],
               catalog=catalog) == ['TRIGGER']


def test_a_text_value_compares_against_value_text(connection, catalog):
    assert ids(connection, [SpellFilter('c', 'material', '=', 'fire')],
               catalog=catalog) == ['HOMING']
    assert ids(connection, [SpellFilter('c', 'extra_entities', 'contains', 'spark')],
               catalog=catalog) == ['HOMING']


def test_an_effect_with_no_value_means_it_writes_that_key(connection, catalog):
    assert ids(connection, [SpellFilter('c', 'fire_rate_wait')],
               catalog=catalog) == ['HOMING', 'TRIGGER']
    assert ids(connection, [SpellFilter('shot_effects')],
               catalog=catalog) == ['HOMING']


def test_the_raw_effect_table_is_the_escape_hatch(connection, catalog):
    assert ids(connection, [SpellFilter('effect', 'op', '=', 'mul')],
               catalog=catalog) == ['HOMING']
    # A write inside an if or a loop does not always apply.
    assert ids(connection, [SpellFilter('effect', 'conditional', '=', '1')],
               catalog=catalog) == ['HOMING']
    assert ids(connection, [SpellFilter('effect', 'key', 'contains', 'reload')],
               catalog=catalog) == ['LIGHT_BULLET']


def test_link_filters_find_the_entities_a_spell_spawns(connection, catalog):
    assert ids(connection, [SpellFilter('projectile', 'entity_file',
                                        'contains', 'fuse')],
               catalog=catalog) == ['TRIGGER']
    assert ids(connection, [SpellFilter('projectile', 'kind', '=',
                                        'add_projectile_trigger_timer')],
               catalog=catalog) == ['TRIGGER']
    assert ids(connection, [SpellFilter('extra_entity')],
               catalog=catalog) == ['HOMING']
    assert ids(connection, [SpellFilter('related', 'kind', '=', 'extra_entity')],
               catalog=catalog) == ['HOMING']


def test_combinators_apply_across_sources(connection, catalog):
    filters = [SpellFilter('spell', 'mana', '<=', '5'),
               SpellFilter('c', 'screenshake')]
    assert ids(connection, filters, 'AND', catalog=catalog) == ['LIGHT_BULLET']
    assert ids(connection, filters, 'OR', catalog=catalog) == \
        ['LIGHT_BULLET', 'TRIGGER']
    # LIGHT_BULLET satisfies both, so exactly-one leaves TRIGGER.
    assert ids(connection, filters, 'XOR', catalog=catalog) == ['TRIGGER']


def test_a_null_column_does_not_poison_an_or(connection, catalog):
    # never_unlimited is NULL for two of the three spells; NULL + 1 is NULL,
    # so the sum has to force each term to 0 or 1 first.
    filters = [SpellFilter('spell', 'never_unlimited', '=', '1'),
               SpellFilter('spell', 'mana', '=', '1')]
    assert ids(connection, filters, 'OR', catalog=catalog) == ['HOMING', 'TRIGGER']


def test_the_text_scope_searches_name_id_and_description(connection, catalog):
    assert ids(connection, [], scope=SpellScope('spark'), catalog=catalog) == \
        ['LIGHT_BULLET']
    assert ids(connection, [], scope=SpellScope('HOM'), catalog=catalog) == ['HOMING']


def test_unknown_identifiers_never_reach_the_sql(catalog):
    with pytest.raises(FilterError):
        spell_query([SpellFilter('spell', 'no_such_column', '=', '1')],
                    catalog=catalog)
    with pytest.raises(FilterError):
        spell_query([SpellFilter('nonsense', 'mana', '=', '1')], catalog=catalog)


def test_problem_describes_a_half_typed_row(catalog):
    assert problem(SpellFilter(''), catalog) == 'no source'
    assert problem(SpellFilter('spel'), catalog).startswith('unknown source')
    assert problem(SpellFilter('spell'), catalog) == 'no column'
    assert problem(SpellFilter('spell', 'mana'), catalog) is None
    assert problem(SpellFilter('c'), catalog) is None
    assert problem(SpellFilter('projectile'), catalog) is None


def test_detail_reads_one_spell_whole(connection):
    spell_id = connection.execute(
        "SELECT id FROM spells WHERE action_id = 'HOMING'").fetchone()[0]
    properties = dict(spell_detail.properties(connection, spell_id))
    assert properties['mana'] == '20'
    assert 'never_unlimited' in properties          # NULL columns are dropped

    written = [str(effect) for effect in spell_detail.effects(connection, spell_id)]
    assert 'c.speed_multiplier mul 0.75' in written
    assert "c.material set 'fire'" in written
    assert 'c.speed_multiplier min 20   (conditional)' in written

    links = spell_detail.links(connection, spell_id)
    assert [heading for heading, _ in links] == \
        ['extra entities', 'extra entities', 'related']
    assert '→ homing' in links[0][1]


def test_an_unresolved_projectile_is_shown_as_such(connection):
    spell_id = connection.execute(
        "SELECT id FROM spells WHERE action_id = 'TRIGGER'").fetchone()[0]
    lines = [line for _, line in spell_detail.links(connection, spell_id)]
    # `add_projectile(target)` was a variable, so it names no entity.
    assert any(line.endswith('(no entity)') and 'target' in line for line in lines)
    assert any('fuse.xml' in line and 'delay 20' in line for line in lines)

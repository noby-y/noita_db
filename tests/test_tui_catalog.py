"""What the TUI learns from the open database, and nothing hardcoded."""

from __future__ import annotations

import sqlite3

import pytest

from noita_tui import catalog as catalog_module
from noita_tui import db, detail


@pytest.fixture
def connection(built_database):
    connection = db.connect(built_database)
    yield connection
    connection.close()


@pytest.fixture
def catalog(connection):
    return catalog_module.load(connection)


def test_refuses_a_database_from_the_future(built_database):
    connection = sqlite3.connect(built_database)
    connection.execute(f'PRAGMA user_version = {db.SUPPORTED_SCHEMA + 1}')
    connection.close()
    with pytest.raises(db.UnusableDatabase):
        db.connect(built_database)


def test_refuses_something_that_is_not_a_database(tmp_path):
    empty = tmp_path / 'empty.db'
    sqlite3.connect(empty).close()
    with pytest.raises(db.UnusableDatabase):
        db.connect(empty)
    with pytest.raises(db.UnusableDatabase):
        db.connect(tmp_path / 'absent.db')
    # Refusing is not the same as creating: the missing file stays missing.
    assert not (tmp_path / 'absent.db').exists()


def test_only_component_types_with_rows_are_offered(catalog):
    assert 'ProjectileComponent' in catalog.types
    assert all(component.count > 0 for component in catalog.types.values())
    # A config struct is documented but is not a component, so it is not a type
    # anyone can filter on.
    assert 'ConfigExplosion' not in catalog.types


def test_attributes_come_from_the_table_without_the_meta_columns(catalog):
    attributes = catalog.attribute_names('ProjectileComponent')
    assert 'damage' in attributes
    assert 'config_explosion.damage_critical.chance' in attributes
    assert not {'component_id', 'entity_id', 'instance_index', '_enabled', '_tags'} \
        & set(attributes)


def test_declared_types_say_which_operators_make_sense(catalog):
    assert catalog.attribute('ProjectileComponent', 'damage').numeric
    assert not catalog.attribute('LuaComponent', 'script_source_file').numeric


def test_reverse_index_finds_the_component_for_an_attribute(catalog):
    assert catalog.types_by_attribute['image_file'] == ('SpriteComponent',)
    assert 'ProjectileComponent' in catalog.types_by_attribute['damage']


def test_tags_are_read_out_of_the_database(catalog):
    assert 'projectile' in catalog.tags
    assert 'teleportable_NOT' in catalog.tags


def test_distinct_values_are_offered_for_a_column(connection):
    values = catalog_module.distinct_values(
        connection, 'LuaComponent', 'script_source_file')
    assert 'data/scripts/replaced.lua' in values


def test_storage_types_expose_a_text_value_in_an_integer_column(connection):
    kinds = catalog_module.storage_types(
        connection, 'PhysicsImageShapeComponent', 'material')
    assert kinds == {'text': 1}


def test_detail_walks_an_entity_to_its_values(connection, catalog):
    entity_id = connection.execute(
        'SELECT id FROM entities WHERE entity_key = ?',
        ('data/entities/projectiles/light_bullet.xml#',)).fetchone()[0]
    components = detail.components(connection, entity_id)
    assert [component.label for component in components] == [
        'VelocityComponent', 'ProjectileComponent', 'LuaComponent',
        'ProjectileComponent #2', 'SpriteComponent', 'PhysicsImageShapeComponent']

    second = next(c for c in components if c.label == 'ProjectileComponent #2')
    values = dict(detail.values(connection, second, catalog))
    assert values['damage'] == '0.12'
    assert values['config_explosion.explosion_radius'] == '2.0'


def test_detail_reports_what_did_not_fit_the_columns(connection, catalog):
    entity_id = connection.execute(
        'SELECT id FROM entities WHERE entity_key = ?',
        ('data/entities/projectiles/fuse.xml#',)).fetchone()[0]
    assert detail.counts(connection, entity_id) == (0, 2)
    velocity = detail.components(connection, entity_id)[0]
    assert detail.duplicates(connection, velocity.id) == [('mass#1', '3'),
                                                          ('mass#2', '4')]

"""The two tables stage 2 writes."""

from __future__ import annotations

import pytest

from noita_tui import catalog as catalog_module
from noita_tui import db, groups
from noita_tui.query import Filter, Scope, entity_query

FUSE = 'data/entities/projectiles/fuse.xml#'


@pytest.fixture
def connection(built_database):
    connection = db.connect(built_database)
    yield connection
    connection.close()


@pytest.fixture
def catalog(connection):
    return catalog_module.load(connection)


def test_names_are_unique(connection):
    groups.create(connection, 'projectiles')
    with pytest.raises(groups.DuplicateGroup):
        groups.create(connection, 'projectiles')
    with pytest.raises(groups.DuplicateGroup):
        groups.create(connection, '   ')


def test_a_whole_result_goes_in_at_once(connection, catalog):
    group = groups.create(connection, 'has velocity')
    query = entity_query([Filter('VelocityComponent')], catalog=catalog)
    assert groups.add_result(connection, group.id, query) == 3
    # Adding the same result twice does not duplicate the membership.
    assert groups.add_result(connection, group.id, query) == 0
    assert len(groups.members(connection, group.id)) == 3


def test_a_single_entity_goes_in_and_out(connection):
    group = groups.create(connection, 'favourites')
    assert groups.add_entity(connection, group.id, FUSE)
    assert not groups.add_entity(connection, group.id, FUSE)
    assert groups.membership(connection, FUSE) == ['favourites']
    assert groups.remove_entity(connection, group.id, FUSE)
    assert groups.membership(connection, FUSE) == []


def test_deleting_a_group_takes_its_members(connection):
    group = groups.create(connection, 'doomed')
    groups.add_entity(connection, group.id, FUSE)
    groups.delete(connection, group.id)
    assert groups.list_groups(connection) == []
    # The cascade only fires because db.connect turns foreign keys on.
    assert connection.execute(
        'SELECT count(*) FROM entity_group_members').fetchone()[0] == 0


def test_membership_can_outlive_the_entity_it_names(connection):
    group = groups.create(connection, 'stale')
    groups.add_entity(connection, group.id, 'data/entities/from_another_build.xml#')
    orphan = groups.members(connection, group.id)[0]
    assert orphan['entity_key'] == 'data/entities/from_another_build.xml#'
    assert orphan['id'] is None


def test_group_counts_are_listed(connection, catalog):
    group = groups.create(connection, 'sprites')
    groups.add_result(connection, group.id,
                      entity_query([Filter('SpriteComponent')], catalog=catalog))
    listed = groups.list_groups(connection)
    assert [(g.name, g.members) for g in listed] == [('sprites', 2)]


def test_the_scope_and_the_group_agree(connection, catalog):
    group = groups.create(connection, 'lua')
    query = entity_query([Filter('LuaComponent')], catalog=catalog)
    groups.add_result(connection, group.id, query)
    scoped = entity_query([], scope=Scope(group_id=group.id), catalog=catalog)
    sql, params = scoped.select()
    assert sorted(row['name'] for row in connection.execute(sql, params)) == \
        ['base_projectile', 'homing', 'projectile_default']

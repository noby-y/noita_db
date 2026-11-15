"""Exporting a result as a list of entity paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from noita_tui import catalog as catalog_module
from noita_tui import db, export
from noita_tui.query import Filter, Scope, entity_query


@pytest.fixture
def connection(built_database):
    connection = db.connect(built_database)
    yield connection
    connection.close()


@pytest.fixture
def catalog(connection):
    return catalog_module.load(connection)


def test_a_filtered_result_becomes_a_list_of_files(connection, catalog):
    query = entity_query([Filter('LuaComponent')], catalog=catalog)
    assert export.entity_paths(connection, query) == [
        'data/entities/base_projectile.xml',
        'data/entities/misc/homing.xml',
        'data/entities/projectiles/light_bullet.xml',
    ]


def test_a_nested_entity_keeps_its_path(connection, catalog):
    query = entity_query([], scope=Scope(text='base_enemy'), catalog=catalog)
    # The child of fuse.xml, addressable as the file plus its position in it.
    assert export.entity_paths(connection, query) == [
        'data/entities/projectiles/fuse.xml#0']


def test_every_entity_exports_once(connection, catalog):
    paths = export.entity_paths(connection, entity_query([], catalog=catalog))
    assert len(paths) == len(set(paths))
    assert len(paths) == connection.execute(
        'SELECT count(*) FROM entities').fetchone()[0]


def test_writing_is_one_path_per_line(tmp_path: Path):
    target = tmp_path / 'out.txt'
    assert export.write(target, ['data/a.xml', 'data/b.xml#1']) == 2
    assert target.read_text(encoding='utf-8') == 'data/a.xml\ndata/b.xml#1\n'


def test_an_empty_result_writes_an_empty_file(tmp_path: Path):
    target = tmp_path / 'out.txt'
    assert export.write(target, []) == 0
    assert target.read_text(encoding='utf-8') == ''

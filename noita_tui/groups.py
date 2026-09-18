"""The two tables stage 2 writes.

Membership is stored by `entity_key` rather than by row id, because every CLI
run renumbers the entities: `--preserve-groups` carries these two tables across
a rebuild and nothing else. The flip side is that membership can outlive the
entity it names, so listing a group left-joins and shows the orphans instead of
dropping them silently.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .query import Query


class DuplicateGroup(Exception):
    """Group names are unique."""


@dataclass(frozen=True)
class Group:
    id: int
    name: str
    notes: str | None
    created_at: str | None
    members: int


def list_groups(connection: sqlite3.Connection) -> list[Group]:
    rows = connection.execute(
        'SELECT g.id, g.name, g.notes, g.created_at,'
        ' (SELECT count(*) FROM entity_group_members m WHERE m.group_id = g.id)'
        ' AS members FROM entity_groups g ORDER BY g.name')
    return [Group(*row) for row in rows]


def create(connection: sqlite3.Connection, name: str, notes: str | None = None) -> Group:
    name = name.strip()
    if not name:
        raise DuplicateGroup('a group needs a name')
    try:
        with connection:
            row = connection.execute(
                'INSERT INTO entity_groups (name, notes, created_at)'
                " VALUES (?, ?, datetime('now')) RETURNING id, name, notes, created_at",
                (name, notes)).fetchone()
    except sqlite3.IntegrityError as error:
        raise DuplicateGroup(f'a group named {name!r} already exists') from error
    return Group(*row, 0)


def delete(connection: sqlite3.Connection, group_id: int) -> None:
    """The membership rows go too, by way of the ON DELETE CASCADE the schema
    declares — which only fires because `db.connect` turns foreign keys on."""
    with connection:
        connection.execute('DELETE FROM entity_groups WHERE id = ?', (group_id,))


def add_result(connection: sqlite3.Connection, group_id: int, query: Query) -> int:
    """Add every entity the current filter matched, in one statement. Reusing
    the query as a subquery beats round-tripping ids through Python, however
    many rows it matched."""
    with connection:
        cursor = connection.execute(
            'INSERT OR IGNORE INTO entity_group_members (group_id, entity_key)'
            f' SELECT ?, e.entity_key FROM entities e WHERE {query.where}',
            (group_id, *query.params))
    return cursor.rowcount


def add_entity(connection: sqlite3.Connection, group_id: int, entity_key: str) -> bool:
    with connection:
        cursor = connection.execute(
            'INSERT OR IGNORE INTO entity_group_members (group_id, entity_key)'
            ' VALUES (?, ?)', (group_id, entity_key))
    return cursor.rowcount > 0


def remove_entity(connection: sqlite3.Connection, group_id: int,
                  entity_key: str) -> bool:
    with connection:
        cursor = connection.execute(
            'DELETE FROM entity_group_members WHERE group_id = ? AND entity_key = ?',
            (group_id, entity_key))
    return cursor.rowcount > 0


def members(connection: sqlite3.Connection, group_id: int) -> list[sqlite3.Row]:
    """Left-joined: a key left over from a build against a different game
    version has no entity, and is worth showing as such."""
    return connection.execute(
        'SELECT m.entity_key, e.id, e.name, e.display_name, e.source_file'
        ' FROM entity_group_members m LEFT JOIN entities e'
        ' ON e.entity_key = m.entity_key WHERE m.group_id = ?'
        ' ORDER BY e.name IS NULL DESC, e.name, m.entity_key',
        (group_id,)).fetchall()


def membership(connection: sqlite3.Connection, entity_key: str) -> list[str]:
    return [row[0] for row in connection.execute(
        'SELECT g.name FROM entity_groups g JOIN entity_group_members m'
        ' ON m.group_id = g.id WHERE m.entity_key = ? ORDER BY g.name',
        (entity_key,))]

"""Everything the detail pane shows about one entity.

`components.id` is the `component_id` of the row in the typed table, so walking
from an entity to its components to their values is three lookups and no
search. A component with `documented = 0` has no typed table at all — its
values live only in `unimplemented_attributes` — which is why the values call
returns an empty list rather than assuming a table exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .catalog import META_COLUMNS, Catalog
from .db import quote


@dataclass(frozen=True)
class ComponentRow:
    id: int
    component_type: str
    instance_index: int
    documented: bool
    enabled: int | None
    tags: str | None

    @property
    def label(self) -> str:
        return (f'{self.component_type} #{self.instance_index + 1}'
                if self.instance_index else self.component_type)


def entity(connection: sqlite3.Connection, entity_id: int) -> sqlite3.Row | None:
    return connection.execute(
        'SELECT * FROM entities WHERE id = ?', (entity_id,)).fetchone()


def children(connection: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        'SELECT id, name, display_name, entity_path FROM entities'
        ' WHERE parent_id = ? ORDER BY entity_path', (entity_id,)).fetchall()


def bases(connection: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        'SELECT base_file, found FROM entity_bases WHERE entity_id = ?'
        ' ORDER BY ordinal', (entity_id,)).fetchall()


def components(connection: sqlite3.Connection, entity_id: int) -> list[ComponentRow]:
    rows = connection.execute(
        'SELECT id, component_type, instance_index, documented, _enabled, _tags'
        ' FROM components WHERE entity_id = ? ORDER BY id', (entity_id,))
    return [ComponentRow(row['id'], row['component_type'], row['instance_index'],
                         bool(row['documented']), row['_enabled'], row['_tags'])
            for row in rows]


def values(connection: sqlite3.Connection, component: ComponentRow,
           catalog: Catalog) -> list[tuple[str, str]]:
    known = catalog.get(component.component_type)
    if known is None or not known.has_table:
        return []
    row = connection.execute(
        f'SELECT * FROM {quote(component.component_type)} WHERE component_id = ?',
        (component.id,)).fetchone()
    if row is None:
        return []
    return [(key, _text(row[key])) for key in row.keys() if key not in META_COLUMNS]


def unimplemented(connection: sqlite3.Connection,
                  component_id: int) -> list[tuple[str, str]]:
    return [(row['path'], _text(row['value'])) for row in connection.execute(
        'SELECT path, value FROM unimplemented_attributes WHERE component_id = ?'
        ' ORDER BY id', (component_id,))]


def duplicates(connection: sqlite3.Connection,
               component_id: int) -> list[tuple[str, str]]:
    return [(row['key'], _text(row['value'])) for row in connection.execute(
        'SELECT key, value FROM duplicate_attributes WHERE component_id = ?'
        ' ORDER BY id', (component_id,))]


def counts(connection: sqlite3.Connection, entity_id: int) -> tuple[int, int]:
    """How much of this entity did not fit the typed columns."""
    return (
        connection.execute(
            'SELECT count(*) FROM unimplemented_attributes WHERE entity_id = ?',
            (entity_id,)).fetchone()[0],
        connection.execute(
            'SELECT count(*) FROM duplicate_attributes WHERE entity_id = ?',
            (entity_id,)).fetchone()[0])


def _text(value: object) -> str:
    return '' if value is None else str(value)

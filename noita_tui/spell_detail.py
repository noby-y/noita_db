"""Everything the detail pane shows about one spell.

The entity links are left-joined on purpose. `entity_id` is NULL when the path
resolved to nothing, and for the 12 `add_projectile` calls whose argument was a
Lua expression rather than a literal path — `"data/entities/items/pickup/" ..
egg_name`. Both are worth showing as what they are instead of hiding behind an
inner join.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Effect:
    scope: str
    key: str
    op: str
    value: str
    conditional: bool

    def __str__(self) -> str:
        line = f'{self.scope}.{self.key} {self.op} {self.value}'.rstrip()
        return line + ('   (conditional)' if self.conditional else '')


def spell(connection: sqlite3.Connection, spell_id: int) -> sqlite3.Row | None:
    return connection.execute(
        'SELECT * FROM spells WHERE id = ?', (spell_id,)).fetchone()


def properties(connection: sqlite3.Connection, spell_id: int) -> list[tuple[str, str]]:
    row = spell(connection, spell_id)
    if row is None:
        return []
    return [(key, str(row[key])) for key in row.keys()
            if key != 'id' and row[key] is not None]


def effects(connection: sqlite3.Connection, spell_id: int) -> list[Effect]:
    rows = connection.execute(
        'SELECT scope, key, op, value_num, value_text, conditional'
        ' FROM spell_effects WHERE spell_id = ? ORDER BY id', (spell_id,))
    return [Effect(row['scope'], row['key'], row['op'],
                   _value(row['value_num'], row['value_text']),
                   bool(row['conditional'])) for row in rows]


def links(connection: sqlite3.Connection, spell_id: int) -> list[tuple[str, str]]:
    """The three link tables as (heading, line) pairs, in one list."""
    lines = []
    for row in connection.execute(
            'SELECT l.kind, l.entity_file, l.draw_count, l.delay_frames, e.name'
            ' FROM spell_projectile_links l LEFT JOIN entities e ON e.id = l.entity_id'
            ' WHERE l.spell_id = ? ORDER BY l.id', (spell_id,)):
        extra = ''
        if row['draw_count'] is not None:
            extra += f'  draw {row["draw_count"]}'
        if row['delay_frames'] is not None:
            extra += f'  delay {row["delay_frames"]}'
        lines.append(('projectiles',
                      f'{row["kind"]}  {row["entity_file"]}'
                      f'{_entity(row["name"])}{extra}'))
    for row in connection.execute(
            'SELECT l.entity_file, e.name FROM spell_extra_entity_links l'
            ' LEFT JOIN entities e ON e.id = l.entity_id'
            ' WHERE l.spell_id = ? ORDER BY l.id', (spell_id,)):
        lines.append(('extra entities',
                      f'{row["entity_file"]}{_entity(row["name"])}'))
    for row in connection.execute(
            'SELECT l.kind, l.entity_file, l.count, e.name FROM spell_related_entities l'
            ' LEFT JOIN entities e ON e.id = l.entity_id'
            ' WHERE l.spell_id = ? ORDER BY l.id', (spell_id,)):
        count = f'  ×{row["count"]}' if row['count'] else ''
        lines.append(('related',
                      f'{row["kind"]}  {row["entity_file"]}'
                      f'{_entity(row["name"])}{count}'))
    return lines


def _value(number: float | None, text: str | None) -> str:
    if text is not None:
        return repr(text)
    if number is None:
        return ''
    return str(int(number)) if float(number).is_integer() else str(number)


def _entity(name: str | None) -> str:
    return f'  → {name}' if name else '  (no entity)'

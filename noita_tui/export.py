"""A result set out, as a list of entity paths.

The path is the form the game files use — `data/entities/projectiles/fuse.xml`,
with `#0/1` appended for a nested `<Entity>`, which is `entities.entity_key`
without the `#` a root entity does not need. That makes the export something a
mod, a grep or `<Base file="…">` can take verbatim.

The current query goes in rather than the table on screen: the result is
whatever the filters matched, not whatever happens to be scrolled into view.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .query import Query


def entity_paths(connection: sqlite3.Connection, query: Query) -> list[str]:
    """Every match, ordered by file so the list diffs cleanly between runs."""
    sql, params = query.select(columns='e.source_file, e.entity_path',
                               order='e.source_file, e.entity_path')
    return [path(row['source_file'], row['entity_path'])
            for row in connection.execute(sql, params)]


def path(source_file: str, entity_path: str) -> str:
    return source_file if entity_path == '' else f'{source_file}#{entity_path}'


def write(target: Path, paths: list[str]) -> int:
    """One path per line, newline-terminated. Returns what was written."""
    target.write_text(''.join(f'{line}\n' for line in paths), encoding='utf-8')
    return len(paths)

"""Opening the database, and the one string operation everything else needs.

Stage 1 owns the file format, so this module never creates a database: opening
a path that holds no schema is an error worth reporting, not something to paper
over with an empty file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# The highest `PRAGMA user_version` this package knows how to read. A newer
# database may have renamed the tables underneath us, so refuse it.
SUPPORTED_SCHEMA = 1


class UnusableDatabase(Exception):
    """The path is not a noita-db database, or is one from a future stage 1."""


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    if not path.exists():
        raise UnusableDatabase(f'{path}: no such file — run noita-db to build it')
    # mode=rw rather than the default rwc: a typo in the path should fail here
    # rather than leave an empty database behind.
    connection = sqlite3.connect(f'file:{path}?mode=rw', uri=True)
    connection.row_factory = sqlite3.Row
    # entity_group_members declares ON DELETE CASCADE, which SQLite ignores
    # unless asked, per connection. Without it a deleted group leaves its
    # members behind to attach themselves to the next group that reuses the id.
    connection.execute('PRAGMA foreign_keys = ON')

    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == 0 or not _has_table(connection, 'entities'):
        connection.close()
        raise UnusableDatabase(f'{path}: not a noita-db database')
    if version > SUPPORTED_SCHEMA:
        connection.close()
        raise UnusableDatabase(
            f'{path}: schema version {version}, this build understands '
            f'{SUPPORTED_SCHEMA}')
    return connection


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,)).fetchone() is not None


def build_info(connection: sqlite3.Connection) -> dict[str, str]:
    return {row['key']: row['value']
            for row in connection.execute('SELECT key, value FROM build_info')}


def quote(identifier: str) -> str:
    """SQLite cannot parametrise a table or column name, and component columns
    are XML attribute paths with dots in them, so every one has to be quoted."""
    return '"' + identifier.replace('"', '""') + '"'

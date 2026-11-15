"""Build the database from a Noita data root.

Every run rebuilds from scratch; there is no incremental path. The only thing
that can survive a rebuild is entity groups, and only when asked for. One group
is not carried but derived: `db.SPELL_PROJECTILE_GROUP` is rewritten every run
from the entities the spells fire.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import comp_docs, db, names, spells
from .entities import EntityResolver

DATA_DIR = 'data'
COMP_DOCS_DIR = 'comp_docs'
COMMON_CSV = 'common.csv'
GUN_ACTIONS = 'data/scripts/gun/gun_actions.lua'


@dataclass
class BuildResult:
    entities: int
    components: int
    spells: int
    unimplemented: int
    duplicates: int
    warnings: int


class MissingInput(Exception):
    pass


def check_inputs(root: Path) -> None:
    required = [root / DATA_DIR, root / COMP_DOCS_DIR, root / COMMON_CSV,
                root / GUN_ACTIONS]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise MissingInput(
            'missing under %s: %s' % (root, ', '.join(str(p.relative_to(root))
                                                      for p in missing)))


def build(root: Path, db_path: Path, preserve_groups: bool = False,
          languages: tuple[str, ...] = names.DEFAULT_LANGUAGES,
          log=lambda message: None) -> BuildResult:
    check_inputs(root)

    saved_groups = db.read_groups(db_path) if preserve_groups else db.SavedGroups([], [])
    if saved_groups.groups:
        log(f'preserving {len(saved_groups.groups)} entity group(s)')

    log('reading comp_docs')
    docs = comp_docs.load(root / COMP_DOCS_DIR)
    log(f'  {len(docs.components)} component types')

    log('reading common.csv')
    translations = names.load(root / COMMON_CSV, languages)
    log(f'  {len(translations.entries)} strings')

    log('finding entity files')
    resolver = EntityResolver(root, docs)
    files = resolver.find_entity_files()
    log(f'  {len(files)} entity files')

    log('resolving entities')
    roots = [entity for entity in (resolver.resolve(path) for path in files)
             if entity is not None]

    log('parsing gun_actions.lua')
    spell_list = spells.load(root / GUN_ACTIONS)
    log(f'  {len(spell_list)} spells')
    columns = db.spell_columns(spell_list)

    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute('PRAGMA journal_mode = OFF')
        connection.execute('PRAGMA synchronous = OFF')
        writer = db.DatabaseWriter(connection, docs, translations)
        log('creating schema')
        writer.create_schema(columns)
        writer.write_translations()
        log('writing entities')
        writer.write_entities(roots)
        log('writing spells')
        writer.write_spells(spell_list, columns)
        writer.write_warnings(resolver.warnings)
        writer.restore_groups(saved_groups)
        grouped = writer.write_spell_projectile_group()
        log(f'grouping {grouped} entities as {db.SPELL_PROJECTILE_GROUP!r}')

        result = BuildResult(
            entities=_count(connection, 'entities'),
            components=_count(connection, 'components'),
            spells=_count(connection, 'spells'),
            unimplemented=_count(connection, 'unimplemented_attributes'),
            duplicates=_count(connection, 'duplicate_attributes'),
            warnings=_count(connection, 'parse_warnings'),
        )
        writer.write_build_info({
            'root': root.resolve(),
            'entity_files': len(files),
            'entities': result.entities,
            'components': result.components,
            'spells': result.spells,
        })
        connection.commit()
        connection.execute('VACUUM')
    finally:
        connection.close()
    return result


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f'SELECT count(*) FROM {db.quote(table)}').fetchone()[0]

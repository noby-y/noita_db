"""SQLite schema and writer.

Because `comp_docs/` pins down the component set, every component type gets its
own table with typed columns — `SELECT * FROM ProjectileComponent WHERE damage
> 5` rather than a key-value join. Column names are the XML attribute paths
verbatim, dots included (`"config_explosion.damage"`), so the mapping between a
row and the file it came from stays obvious.

Everything the typed columns cannot hold still lands somewhere:
`unimplemented_attributes` for undocumented attributes, `duplicate_attributes`
for repeated ones, `parse_warnings` for values that would not convert.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import comp_docs
from .comp_docs import ComponentDocs, Leaf
from .entities import ComponentInstance, ResolvedEntity
from .names import Translations, strip_prefix
from .spells import Spell

SCHEMA_VERSION = 1

# Columns every component table carries, ahead of its documented fields.
COMPONENT_META = (
    ('component_id', 'INTEGER PRIMARY KEY'),
    ('entity_id', 'INTEGER NOT NULL'),
    ('instance_index', 'INTEGER NOT NULL'),
    ('_enabled', 'INTEGER'),
    ('_tags', 'TEXT'),
)

CORE_SCHEMA = """
CREATE TABLE build_info (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE translations (
    code_name TEXT NOT NULL,
    language  TEXT NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (code_name, language)
);

CREATE TABLE entities (
    id          INTEGER PRIMARY KEY,
    entity_key  TEXT NOT NULL UNIQUE,  -- source_file#entity_path; stable across rebuilds
    source_file TEXT NOT NULL,         -- the entity file this tree was built from
    defined_in  TEXT NOT NULL,         -- where the element was written; differs for a
                                       -- nested entity inherited through a <Base>
    entity_path TEXT NOT NULL,         -- '' for a file's root entity, '0', '0/1' for nested
    parent_id   INTEGER REFERENCES entities(id),
    name        TEXT NOT NULL,         -- name attribute, or the file stem
    name_key    TEXT,                  -- raw name attribute, '$' included
    display_name TEXT,                 -- name_key resolved through common.csv
    tags        TEXT NOT NULL,         -- comma-joined, bases first
    serialize   TEXT
);

CREATE TABLE entity_tags (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    tag       TEXT NOT NULL,
    ordinal   INTEGER NOT NULL,
    PRIMARY KEY (entity_id, tag)
);

CREATE TABLE entity_bases (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    ordinal   INTEGER NOT NULL,
    base_file TEXT NOT NULL,
    found     INTEGER NOT NULL,        -- 0 when the target file is missing
    PRIMARY KEY (entity_id, ordinal)
);

CREATE TABLE components (
    id             INTEGER PRIMARY KEY,
    entity_id      INTEGER NOT NULL REFERENCES entities(id),
    component_type TEXT NOT NULL,
    instance_index INTEGER NOT NULL,
    documented     INTEGER NOT NULL,   -- 0 when no comp_docs entry exists
    _enabled       INTEGER,
    _tags          TEXT
);

CREATE TABLE unimplemented_attributes (
    id             INTEGER PRIMARY KEY,
    entity_id      INTEGER NOT NULL REFERENCES entities(id),
    component_id   INTEGER REFERENCES components(id),
    component_type TEXT NOT NULL,
    path           TEXT NOT NULL,
    value          TEXT
);

CREATE TABLE duplicate_attributes (
    id             INTEGER PRIMARY KEY,
    entity_id      INTEGER NOT NULL REFERENCES entities(id),
    component_id   INTEGER NOT NULL REFERENCES components(id),
    component_type TEXT NOT NULL,
    key            TEXT NOT NULL,      -- 'damage#1', 'damage#2', ...
    path           TEXT NOT NULL,
    occurrence     INTEGER NOT NULL,
    value          TEXT
);

CREATE TABLE parse_warnings (
    id          INTEGER PRIMARY KEY,
    source_file TEXT,
    message     TEXT NOT NULL
);

CREATE TABLE spell_effects (
    id          INTEGER PRIMARY KEY,
    spell_id    INTEGER NOT NULL REFERENCES spells(id),
    scope       TEXT NOT NULL,         -- 'c', 'shot_effects', 'global'
    key         TEXT NOT NULL,
    op          TEXT NOT NULL,         -- set/add/mul/concat/min/max/expr
    value_num   REAL,                  -- negative for a subtraction, op stays 'add'
    value_text  TEXT,
    conditional INTEGER NOT NULL       -- applied only inside an if/loop
);

CREATE TABLE spell_projectile_links (
    id           INTEGER PRIMARY KEY,
    spell_id     INTEGER NOT NULL REFERENCES spells(id),
    kind         TEXT NOT NULL,        -- the add_projectile* call used
    entity_file  TEXT,                 -- the Lua expression when not a literal path
    entity_id    INTEGER REFERENCES entities(id),
    draw_count   INTEGER,
    delay_frames INTEGER
);

CREATE TABLE spell_extra_entity_links (
    id          INTEGER PRIMARY KEY,
    spell_id    INTEGER NOT NULL REFERENCES spells(id),
    entity_file TEXT NOT NULL,
    entity_id   INTEGER REFERENCES entities(id)
);

CREATE TABLE spell_related_entities (
    id          INTEGER PRIMARY KEY,
    spell_id    INTEGER NOT NULL REFERENCES spells(id),
    kind        TEXT NOT NULL,         -- 'projectile' or 'extra_entity'
    entity_file TEXT NOT NULL,
    entity_id   INTEGER REFERENCES entities(id),
    count       INTEGER
);

CREATE TABLE entity_groups (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    notes   TEXT,
    created_at TEXT
);

CREATE TABLE entity_group_members (
    group_id   INTEGER NOT NULL REFERENCES entity_groups(id) ON DELETE CASCADE,
    entity_key TEXT NOT NULL,          -- by key, so membership survives a rebuild
    PRIMARY KEY (group_id, entity_key)
);
"""

CORE_INDEXES = """
CREATE INDEX idx_entities_name ON entities(name);
CREATE INDEX idx_entities_display_name ON entities(display_name);
CREATE INDEX idx_entities_source ON entities(source_file);
CREATE INDEX idx_entities_defined_in ON entities(defined_in);
CREATE INDEX idx_entity_tags_tag ON entity_tags(tag);
CREATE INDEX idx_components_entity ON components(entity_id);
CREATE INDEX idx_components_type ON components(component_type);
CREATE INDEX idx_unimplemented_path ON unimplemented_attributes(component_type, path);
CREATE INDEX idx_spell_effects_key ON spell_effects(key, value_num);
CREATE INDEX idx_spell_effects_spell ON spell_effects(spell_id);
CREATE INDEX idx_spell_projectiles_spell ON spell_projectile_links(spell_id);
CREATE INDEX idx_spell_extra_spell ON spell_extra_entity_links(spell_id);
"""


@dataclass
class SavedGroups:
    groups: list[tuple]
    members: list[tuple]


def read_groups(path: Path) -> SavedGroups:
    """Pull existing groups out of a database before it is rebuilt."""
    if not path.exists():
        return SavedGroups([], [])
    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'entity_groups' not in tables:
            return SavedGroups([], [])
        groups = connection.execute(
            'SELECT id, name, notes, created_at FROM entity_groups').fetchall()
        members = connection.execute(
            'SELECT group_id, entity_key FROM entity_group_members').fetchall() \
            if 'entity_group_members' in tables else []
        return SavedGroups(list(groups), list(members))
    finally:
        connection.close()


class DatabaseWriter:
    def __init__(self, connection: sqlite3.Connection, docs: ComponentDocs,
                 translations: Translations) -> None:
        self.connection = connection
        self.docs = docs
        self.translations = translations
        self._entity_id_by_file: dict[str, int] = {}
        self._next_entity_id = 1
        self._next_component_id = 1

    # -- schema ------------------------------------------------------------

    def create_schema(self, spell_columns: list[tuple[str, str]]) -> None:
        self.connection.executescript(CORE_SCHEMA)
        self.connection.execute(
            'CREATE TABLE spells (%s)' % ', '.join(
                ['id INTEGER PRIMARY KEY',
                 'action_id TEXT NOT NULL UNIQUE',
                 'display_name TEXT',
                 'display_description TEXT']
                + [f'{quote(name)} {sql_type}' for name, sql_type in spell_columns]))
        for doc in self.docs.components:
            self.connection.execute(self._component_table_sql(doc.name, doc.leaves))
            self.connection.execute(
                f'CREATE INDEX {quote("idx_" + doc.name + "_entity")} '
                f'ON {quote(doc.name)}(entity_id)')
        self.connection.executescript(CORE_INDEXES)
        self.connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    @staticmethod
    def _component_table_sql(name: str, leaves: dict[str, Leaf]) -> str:
        columns = [f'{quote(column)} {sql_type}' for column, sql_type in COMPONENT_META]
        columns += [f'{quote(leaf.path)} {leaf.sql_type}' for leaf in leaves.values()]
        return f'CREATE TABLE {quote(name)} ({", ".join(columns)})'

    # -- entities ----------------------------------------------------------

    def write_entities(self, roots: list[ResolvedEntity]) -> None:
        component_rows: dict[str, list[list]] = {}
        entity_rows, tag_rows, base_rows = [], [], []
        component_index, unimplemented, duplicates, warnings = [], [], [], []

        for root in roots:
            self._collect(root, None, entity_rows, tag_rows, base_rows,
                          component_index, component_rows, unimplemented,
                          duplicates, warnings)

        self.connection.executemany(
            'INSERT INTO entities (id, entity_key, source_file, defined_in, entity_path,'
            ' parent_id, name, name_key, display_name, tags, serialize)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?)', entity_rows)
        self.connection.executemany(
            'INSERT INTO entity_tags (entity_id, tag, ordinal) VALUES (?,?,?)', tag_rows)
        self.connection.executemany(
            'INSERT INTO entity_bases (entity_id, ordinal, base_file, found)'
            ' VALUES (?,?,?,?)', base_rows)
        self.connection.executemany(
            'INSERT INTO components (id, entity_id, component_type, instance_index,'
            ' documented, _enabled, _tags) VALUES (?,?,?,?,?,?,?)', component_index)
        self.connection.executemany(
            'INSERT INTO unimplemented_attributes (entity_id, component_id,'
            ' component_type, path, value) VALUES (?,?,?,?,?)', unimplemented)
        self.connection.executemany(
            'INSERT INTO duplicate_attributes (entity_id, component_id, component_type,'
            ' key, path, occurrence, value) VALUES (?,?,?,?,?,?,?)', duplicates)
        self.connection.executemany(
            'INSERT INTO parse_warnings (source_file, message) VALUES (?,?)', warnings)

        for table, rows in component_rows.items():
            doc = self.docs.get(table)
            columns = [name for name, _ in COMPONENT_META] + list(doc.leaves)
            placeholders = ','.join('?' * len(columns))
            self.connection.executemany(
                f'INSERT INTO {quote(table)} ({",".join(quote(c) for c in columns)})'
                f' VALUES ({placeholders})', rows)

    def _collect(self, entity: ResolvedEntity, parent_id: int | None, entity_rows,
                 tag_rows, base_rows, component_index, component_rows,
                 unimplemented, duplicates, warnings, path: str = '',
                 root_file: str | None = None) -> None:
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        # Keyed on the file being built, not on `defined_in`: a child entity
        # inherited through a <Base> would otherwise collide with the copy that
        # lives in the base file itself.
        source_file = root_file or entity.source_file
        entity_key = f'{source_file}#{path}'
        if path == '':
            self._entity_id_by_file.setdefault(source_file, entity_id)

        name = entity.name or f'{Path(source_file).stem}:{path}'
        entity_rows.append((
            entity_id, entity_key, source_file, entity.source_file, path, parent_id,
            strip_prefix(name), entity.name_key,
            self.translations.lookup(entity.name_key), ','.join(entity.tags),
            entity.serialize))
        for ordinal, tag in enumerate(entity.tags):
            tag_rows.append((entity_id, tag, ordinal))
        for ordinal, base in enumerate(entity.bases):
            base_rows.append((entity_id, ordinal, base.file, int(base.found)))
            if not base.found:
                warnings.append((entity.source_file, f'missing base file: {base.file}'))

        counts: dict[str, int] = {}
        for instance in entity.components:
            index = counts.get(instance.type, 0)
            counts[instance.type] = index + 1
            self._write_component(entity, entity_id, instance, index, component_index,
                                  component_rows, unimplemented, duplicates, warnings)

        for child_index, child in enumerate(entity.children):
            child_path = f'{path}/{child_index}' if path else str(child_index)
            self._collect(child, entity_id, entity_rows, tag_rows, base_rows,
                          component_index, component_rows, unimplemented,
                          duplicates, warnings, child_path, source_file)

    def _write_component(self, entity: ResolvedEntity, entity_id: int,
                         instance: ComponentInstance, index: int, component_index,
                         component_rows, unimplemented, duplicates, warnings) -> None:
        component_id = self._next_component_id
        self._next_component_id += 1
        doc = self.docs.get(instance.type)
        documented = doc is not None and doc.is_component

        values: dict[str, object] = (
            {leaf.path: leaf.default for leaf in doc.leaves.values()} if documented else {})
        enabled, tags = None, None
        seen: dict[str, int] = {}

        for path, raw in instance.attrs:
            if path == comp_docs.META_ENABLED:
                enabled = comp_docs.parse_int(raw) if enabled is None else enabled
                continue
            if path == comp_docs.META_TAGS:
                tags = raw if tags is None else tags
                continue
            if path == comp_docs.META_REMOVE_FROM_BASE:
                continue    # consumed during base resolution

            leaf = doc.leaves.get(path) if documented else None
            if leaf is None:
                unimplemented.append((entity_id, component_id, instance.type, path, raw))
                continue

            occurrence = seen.get(path, 0)
            seen[path] = occurrence + 1
            if occurrence:
                # Keep every value: the first is the column, the rest are name#N.
                duplicates.append((entity_id, component_id, instance.type,
                                   f'{path}#{occurrence}', path, occurrence, raw))
                continue
            value, converted = comp_docs.coerce(raw, leaf)
            if not converted and raw.strip() != '':
                warnings.append((entity.source_file,
                                 f'{instance.type}.{path}: {raw!r} is not '
                                 f'{leaf.sql_type}, stored as text'))
            values[path] = value

        for path, rows in instance.containers.items():
            leaf = doc.leaves.get(path) if documented else None
            if leaf is not None and leaf.is_container:
                values[path] = json.dumps(rows, ensure_ascii=False)
            else:
                unimplemented.append((entity_id, component_id, instance.type, path,
                                      json.dumps(rows, ensure_ascii=False)))

        component_index.append((component_id, entity_id, instance.type, index,
                                int(documented), enabled, tags))
        if documented:
            row = [component_id, entity_id, index, enabled, tags]
            row += [values.get(leaf_path) for leaf_path in doc.leaves]
            component_rows.setdefault(instance.type, []).append(row)

    # -- spells ------------------------------------------------------------

    def write_spells(self, spell_list: list[Spell],
                     spell_columns: list[tuple[str, str]]) -> None:
        names = [name for name, _ in spell_columns]
        insert = (
            'INSERT INTO spells (id, action_id, display_name, display_description%s)'
            ' VALUES (?,?,?,?%s)'
            % (''.join(f', {quote(n)}' for n in names), ',?' * len(names)))

        effects, projectiles, extras, related = [], [], [], []
        for spell_id, spell in enumerate(spell_list, start=1):
            properties = spell.properties
            row = [spell_id, spell.action_id,
                   self.translations.lookup(_as_text(properties.get('name'))),
                   self.translations.lookup(_as_text(properties.get('description')))]
            row += [_sql_value(properties.get(name)) for name in names]
            self.connection.execute(insert, row)

            for effect in spell.effects:
                effects.append((spell_id, effect.scope, effect.key, effect.op,
                                effect.value_num, effect.value_text,
                                int(effect.conditional)))
            for link in spell.projectiles:
                # An argument that is not a literal path is kept as its Lua text,
                # which of course resolves to no entity.
                projectiles.append((spell_id, link.kind,
                                    link.entity_file or link.expression,
                                    self._entity_id_by_file.get(link.entity_file or ''),
                                    link.draw_count, link.delay_frames))
            for path in spell.extra_entities:
                extras.append((spell_id, path, self._entity_id_by_file.get(path)))
            for path, count in spell.related_projectiles:
                related.append((spell_id, 'projectile', path,
                                self._entity_id_by_file.get(path), count))
            for path in spell.related_extra_entities:
                related.append((spell_id, 'extra_entity', path,
                                self._entity_id_by_file.get(path), None))

        self.connection.executemany(
            'INSERT INTO spell_effects (spell_id, scope, key, op, value_num,'
            ' value_text, conditional) VALUES (?,?,?,?,?,?,?)', effects)
        self.connection.executemany(
            'INSERT INTO spell_projectile_links (spell_id, kind, entity_file, entity_id,'
            ' draw_count, delay_frames) VALUES (?,?,?,?,?,?)', projectiles)
        self.connection.executemany(
            'INSERT INTO spell_extra_entity_links (spell_id, entity_file, entity_id)'
            ' VALUES (?,?,?)', extras)
        self.connection.executemany(
            'INSERT INTO spell_related_entities (spell_id, kind, entity_file, entity_id,'
            ' count) VALUES (?,?,?,?,?)', related)

    # -- odds and ends -----------------------------------------------------

    def write_translations(self) -> None:
        self.connection.executemany(
            'INSERT OR REPLACE INTO translations (code_name, language, text)'
            ' VALUES (?,?,?)',
            [(code, language, text)
             for (code, language), text in self.translations.entries.items()])

    def write_warnings(self, warnings: list[tuple[str, str]]) -> None:
        self.connection.executemany(
            'INSERT INTO parse_warnings (source_file, message) VALUES (?,?)', warnings)

    def write_build_info(self, info: dict[str, object]) -> None:
        self.connection.executemany(
            'INSERT OR REPLACE INTO build_info (key, value) VALUES (?,?)',
            [(key, str(value)) for key, value in info.items()]
            + [('built_at', datetime.now(timezone.utc).isoformat(timespec='seconds')),
               ('schema_version', str(SCHEMA_VERSION))])

    def restore_groups(self, saved: SavedGroups) -> None:
        self.connection.executemany(
            'INSERT INTO entity_groups (id, name, notes, created_at) VALUES (?,?,?,?)',
            saved.groups)
        self.connection.executemany(
            'INSERT INTO entity_group_members (group_id, entity_key) VALUES (?,?)',
            saved.members)


def spell_columns(spell_list: list[Spell]) -> list[tuple[str, str]]:
    """Derive the `spells` table columns from the keys the file actually uses,
    so a new key in a future game version becomes a column instead of a loss."""
    kinds: dict[str, set[type]] = {}
    for spell in spell_list:
        for key, value in spell.properties.items():
            if key == 'id':
                continue    # already stored as spells.action_id
            kinds.setdefault(key, set()).add(type(value))
    columns = []
    for key in sorted(kinds):
        types = kinds[key]
        if types <= {bool, int}:
            sql_type = comp_docs.INTEGER
        elif types <= {bool, int, float}:
            sql_type = comp_docs.REAL
        else:
            sql_type = comp_docs.TEXT
        columns.append((key, sql_type))
    return columns


def _sql_value(value: object) -> object:
    if isinstance(value, bool):
        return int(value)
    return value


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'

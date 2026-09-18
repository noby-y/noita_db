"""What the open database contains: component types, their attributes, types.

Read out of the file rather than hardcoded, so a rebuild against a patched game
version autocompletes the new fields without a code change. Only types that
have rows are offered — `comp_docs/` documents 28 components the shipped data
never uses, and picking one of those would produce an empty result with no
explanation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .db import quote

# The five columns every component table carries ahead of its attributes.
META_COLUMNS = frozenset(
    {'component_id', 'entity_id', 'instance_index', '_enabled', '_tags'})

NUMERIC_AFFINITIES = frozenset({'INTEGER', 'REAL'})


@dataclass(frozen=True)
class Attribute:
    name: str
    declared_type: str

    @property
    def numeric(self) -> bool:
        return self.declared_type.upper() in NUMERIC_AFFINITIES


@dataclass(frozen=True)
class ComponentType:
    name: str
    count: int
    documented: bool
    attributes: tuple[Attribute, ...] = ()

    @property
    def has_table(self) -> bool:
        return bool(self.attributes)


@dataclass
class Catalog:
    """Built once at startup: 166 `table_info` calls, about 3600 rows."""

    types: dict[str, ComponentType] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    types_by_attribute: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # -- lookups -----------------------------------------------------------

    def get(self, component_type: str) -> ComponentType | None:
        return self.types.get(component_type)

    def attribute(self, component_type: str, name: str) -> Attribute | None:
        component = self.types.get(component_type)
        if component is None:
            return None
        return next((a for a in component.attributes if a.name == name), None)

    @property
    def type_names(self) -> list[str]:
        return list(self.types)

    def attribute_names(self, component_type: str) -> list[str]:
        component = self.types.get(component_type)
        return [a.name for a in component.attributes] if component else []


def load(connection: sqlite3.Connection) -> Catalog:
    tables = {row['name'] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}

    types: dict[str, ComponentType] = {}
    counts = connection.execute(
        'SELECT component_type, count(*) AS n, min(documented) AS documented'
        ' FROM components GROUP BY component_type ORDER BY component_type')
    for row in counts:
        name = row['component_type']
        attributes = (_attributes(connection, name) if name in tables else ())
        types[name] = ComponentType(name, row['n'], bool(row['documented']), attributes)

    # The reverse index is free at this point, and lets someone who types
    # `damage` without knowing the component still get somewhere.
    by_attribute: dict[str, list[str]] = {}
    for component in types.values():
        for attribute in component.attributes:
            by_attribute.setdefault(attribute.name, []).append(component.name)

    tags = tuple(row[0] for row in connection.execute(
        'SELECT DISTINCT tag FROM entity_tags ORDER BY tag'))
    return Catalog(types, tags,
                   {name: tuple(owners) for name, owners in by_attribute.items()})


def _attributes(connection: sqlite3.Connection, table: str) -> tuple[Attribute, ...]:
    return tuple(
        Attribute(row['name'], row['type'])
        for row in connection.execute(f'PRAGMA table_info({quote(table)})')
        if row['name'] not in META_COLUMNS)


# The link tables, under the name the filter UI calls them by.
LINK_TABLES = {
    'projectile': 'spell_projectile_links',
    'extra_entity': 'spell_extra_entity_links',
    'related': 'spell_related_entities',
}
SPELL_SOURCE = 'spell'          # a column of the spells row itself
EFFECT_SOURCE = 'effect'        # a raw spell_effects column, the escape hatch


@dataclass
class SpellCatalog:
    """The spell half, read the same way: `spells` columns come from the keys
    `gun_actions.lua` actually uses, so they are data, not a constant."""

    spells: int = 0
    columns: tuple[Attribute, ...] = ()
    effect_scopes: tuple[str, ...] = ()
    effect_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    effect_columns: tuple[Attribute, ...] = ()
    link_columns: dict[str, tuple[Attribute, ...]] = field(default_factory=dict)

    @property
    def sources(self) -> list[str]:
        return ([SPELL_SOURCE] + list(self.effect_scopes) + [EFFECT_SOURCE]
                + [name for name in LINK_TABLES if name in self.link_columns])

    def keys(self, source: str) -> list[str]:
        """What the second field of a spell filter can hold, given the first."""
        if source == SPELL_SOURCE:
            return [column.name for column in self.columns]
        if source in self.effect_keys:
            return list(self.effect_keys[source])
        if source == EFFECT_SOURCE:
            return [column.name for column in self.effect_columns]
        return [column.name for column in self.link_columns.get(source, ())]

    def column(self, source: str, name: str) -> Attribute | None:
        if source == SPELL_SOURCE:
            columns = self.columns
        elif source == EFFECT_SOURCE:
            columns = self.effect_columns
        else:
            columns = self.link_columns.get(source, ())
        return next((column for column in columns if column.name == name), None)


def load_spells(connection: sqlite3.Connection) -> SpellCatalog:
    tables = {row['name'] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if 'spells' not in tables:
        return SpellCatalog()

    scopes = tuple(row[0] for row in connection.execute(
        'SELECT DISTINCT scope FROM spell_effects ORDER BY scope'))
    keys: dict[str, tuple[str, ...]] = {}
    for scope in scopes:
        keys[scope] = tuple(row[0] for row in connection.execute(
            'SELECT DISTINCT key FROM spell_effects WHERE scope = ? ORDER BY key',
            (scope,)))
    links = {name: _columns(connection, table, {'id', 'spell_id'})
             for name, table in LINK_TABLES.items() if table in tables}
    return SpellCatalog(
        spells=connection.execute('SELECT count(*) FROM spells').fetchone()[0],
        columns=_columns(connection, 'spells', {'id'}),
        effect_scopes=scopes,
        effect_keys=keys,
        effect_columns=_columns(connection, 'spell_effects', {'id', 'spell_id'}),
        link_columns=links)


def _columns(connection: sqlite3.Connection, table: str,
             skip: set[str]) -> tuple[Attribute, ...]:
    return tuple(Attribute(row['name'], row['type'])
                 for row in connection.execute(f'PRAGMA table_info({quote(table)})')
                 if row['name'] not in skip)


def effect_values(connection: sqlite3.Connection, scope: str, key: str,
                  limit: int = 200) -> list[str]:
    """Value completion for an effect filter: the numbers and strings the file
    actually writes to that key."""
    rows = connection.execute(
        'SELECT DISTINCT COALESCE(value_text, value_num) FROM spell_effects'
        ' WHERE scope = ? AND key = ? ORDER BY 1 LIMIT ?', (scope, key, limit))
    return [str(row[0]) for row in rows if row[0] is not None]


def distinct_values(connection: sqlite3.Connection, component_type: str,
                    attribute: str, limit: int = 400) -> list[str]:
    """Value autocomplete. Cheap, and it is the only way to discover that
    `material` wants `'wood_prop'` rather than a number."""
    rows = connection.execute(
        f'SELECT DISTINCT {quote(attribute)} FROM {quote(component_type)}'
        f' WHERE {quote(attribute)} IS NOT NULL'
        f' ORDER BY 1 LIMIT {int(limit)}')
    return [str(row[0]) for row in rows if str(row[0]) != '']


def storage_types(connection: sqlite3.Connection, component_type: str,
                  attribute: str) -> dict[str, int]:
    """`typeof()` per column, because affinity lies.

    Roughly 2180 attributes are INTEGER at runtime but name a material or a
    herd in the XML, and SQLite sorts every number before every text value, so
    `WHERE material > 5` silently skips them. The filter UI shows this so the
    user can see why a comparison found nothing.
    """
    rows = connection.execute(
        f'SELECT typeof({quote(attribute)}) AS kind, count(*) AS n'
        f' FROM {quote(component_type)} GROUP BY 1 ORDER BY n DESC')
    return {row['kind']: row['n'] for row in rows}

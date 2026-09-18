"""Spell filters in, one SELECT out.

Same shape as the entity side — `spells` scanned once, a term per filter, the
combinator deciding how they are glued — but the terms come from four places:

    spell.mana <= 20              a column of the spells row
    c.screenshake >= 10           a write to `c.screenshake` in the action body
    effect.op = mul               a raw spell_effects column, for the rest
    projectile.entity_file …      an entity the spell spawns

There is no instance binding here. A spell writes a key as many times as it
likes and each filter is its own `EXISTS`, so two filters on `c.fire_rate_wait`
can be satisfied by two different writes. That is what you want: the writes are
cumulative, not alternatives.

An empty value means "this is set" rather than "this equals nothing", which is
how you ask for every spell that touches `screenshake` at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import EFFECT_SOURCE, LINK_TABLES, SPELL_SOURCE, SpellCatalog
from .db import quote
from .query import (LIKE_ESCAPE_SQL, OPERATORS, Query, coerce, like_escape,
                    FilterError)


@dataclass(frozen=True)
class SpellFilter:
    source: str                 # spell | c | shot_effects | global | effect | a link
    key: str | None = None
    op: str = '='
    value: str = ''


@dataclass(frozen=True)
class SpellScope:
    text: str = ''              # display name, action id or description


def problem(item: SpellFilter, catalog: SpellCatalog) -> str | None:
    """Why this row cannot run yet, in words, or None when it can."""
    if not item.source:
        return 'no source'
    if item.source not in catalog.sources:
        return f'unknown source {item.source}'
    key = (item.key or '').strip()
    if item.source in (SPELL_SOURCE, EFFECT_SOURCE):
        if not key:
            return 'no column'
        if catalog.column(item.source, key) is None:
            return f'{item.source} has no column {key}'
    elif item.source in LINK_TABLES:
        if key and catalog.column(item.source, key) is None:
            return f'{item.source} has no column {key}'
    if item.op not in OPERATORS:
        return f'unknown operator {item.op}'
    return None


def spell_query(filters: list[SpellFilter], combinator: str = 'AND',
                scope: SpellScope = SpellScope(),
                catalog: SpellCatalog | None = None) -> Query:
    catalog = catalog if catalog is not None else SpellCatalog()
    for item in filters:
        failure = problem(item, catalog)
        if failure is not None:
            raise FilterError(failure)

    combinator = combinator.upper()
    terms, params = [], []
    for item in filters:
        sql, values = _term(item, catalog)
        terms.append(sql)
        params += values

    if not terms:
        clause = ''
    elif len(terms) == 1:
        clause = terms[0]
    elif combinator == 'AND':
        clause = ' AND '.join(terms)
    else:
        # A comparison against a NULL column is NULL rather than 0, which would
        # poison the sum, so every term is forced to a 0 or a 1 first.
        total = ' + '.join(f'IFNULL({term}, 0)' for term in terms)
        clause = f'(({total}) >= 1)' if combinator == 'OR' else f'(({total}) = 1)'

    conditions = [clause] if clause else []
    if scope.text:
        pattern = f'%{like_escape(scope.text)}%'
        conditions.append(
            f'(s.display_name LIKE ? {LIKE_ESCAPE_SQL}'
            f' OR s.action_id LIKE ? {LIKE_ESCAPE_SQL}'
            f' OR s.display_description LIKE ? {LIKE_ESCAPE_SQL})')
        params += [pattern, pattern, pattern]
    return Query(' AND '.join(conditions) if conditions else '1',
                 tuple(params), source='spells s')


# -- terms -----------------------------------------------------------------


def _term(item: SpellFilter, catalog: SpellCatalog) -> tuple[str, list]:
    key = (item.key or '').strip()
    if item.source == SPELL_SOURCE:
        column = catalog.column(SPELL_SOURCE, key)
        return _compare(f's.{quote(key)}', item, column.numeric)

    if item.source in catalog.effect_keys:
        return _effect_term(item, key)

    if item.source == EFFECT_SOURCE:
        column = catalog.column(EFFECT_SOURCE, key)
        sql, params = _compare(f'x.{quote(key)}', item, column.numeric)
        return (f'EXISTS (SELECT 1 FROM spell_effects x'
                f' WHERE x.spell_id = s.id AND {sql})', params)

    table = LINK_TABLES[item.source]
    conditions, params = ['l.spell_id = s.id'], []
    if key:
        column = catalog.column(item.source, key)
        sql, values = _compare(f'l.{quote(key)}', item, column.numeric)
        conditions.append(sql)
        params += values
    return (f'EXISTS (SELECT 1 FROM {quote(table)} l'
            f' WHERE {" AND ".join(conditions)})', params)


def _effect_term(item: SpellFilter, key: str) -> tuple[str, list]:
    conditions, params = ['x.spell_id = s.id', 'x.scope = ?'], [item.source]
    if key:
        conditions.append('x.key = ?')
        params.append(key)
    # With no value the term stays at scope and key: "writes this at all".
    if item.value.strip():
        sql, values = _effect_value(item)
        conditions.append(sql)
        params += values
    return (f'EXISTS (SELECT 1 FROM spell_effects x'
            f' WHERE {" AND ".join(conditions)})', params)


def _effect_value(item: SpellFilter) -> tuple[str, list]:
    """A write lands in `value_num` or in `value_text`, never both, so which
    column to compare follows from what the user typed.

    `c.screenshake = c.screenshake + 10.0` is stored as `add 10.0`, which is why
    searching `screenshake = 10` finds it: the operator here compares the value
    written, not the way it was written. `effect.op` is where you say `add`.
    """
    if item.op == 'contains':
        return (f'CAST(COALESCE(x.value_text, x.value_num) AS TEXT) LIKE ?'
                f' {LIKE_ESCAPE_SQL}', [f'%{like_escape(item.value)}%'])
    number = _as_number(item.value)
    if number is None:
        column, value = 'x.value_text', item.value
    else:
        column, value = 'x.value_num', number
    if item.op == '!=':
        return f'{column} IS NOT ?', [value]
    return f'{column} {item.op} ?', [value]


def _compare(column: str, item: SpellFilter, numeric: bool) -> tuple[str, list]:
    if not item.value.strip():
        # Most spell columns are NULL for most spells — `spell.max_uses` with no
        # value is the useful question "which spells are limited at all?".
        return f'{column} IS NOT NULL', []
    if item.op == 'contains':
        return (f'CAST({column} AS TEXT) LIKE ? {LIKE_ESCAPE_SQL}',
                [f'%{like_escape(item.value)}%'])
    value = coerce(item.value, numeric)
    if item.op == '!=':
        return f'{column} IS NOT ?', [value]
    return f'{column} {item.op} ?', [value]


def _as_number(value: str) -> float | int | None:
    text = value.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None

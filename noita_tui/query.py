"""Filters in, one SELECT out.

Every query is the same shape: `entities` scanned once with a correlated
EXISTS per binding, glued together by the combinator. A binding is one instance
of one component type — filters sharing a binding must be satisfied by the same
instance, and two bindings on the same type must be satisfied by different
ones, which is what makes "a SpriteComponent behind the entity *and another*
in front" expressible.

Identifiers are interpolated because SQLite cannot parametrise them, so every
component type and attribute is checked against the catalog first and quoted
after. Values are always bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog
from .db import quote

OPERATORS = ('=', '!=', '<', '<=', '>', '>=', 'contains')
ORDERING_OPERATORS = frozenset({'<', '<=', '>', '>='})
COMBINATORS = ('AND', 'OR', 'XOR')

LIKE_ESCAPE = '\\'
# SQLite reads no escapes inside a string literal, so this is one backslash.
LIKE_ESCAPE_SQL = "ESCAPE '\\'"


class FilterError(Exception):
    """A filter that cannot be compiled — the UI greys these out instead."""


@dataclass(frozen=True)
class Filter:
    """One predicate on one component attribute.

    `attribute` is None for a bare "has this component". `binding` is the
    instance the filter applies to; new filters default to #1 because wanting
    one component to satisfy two conditions is the ordinary case.
    """

    component_type: str
    attribute: str | None = None
    op: str = '='
    value: str = ''
    binding: int = 1


@dataclass(frozen=True)
class Scope:
    """Restrictions that are always ANDed, whatever the combinator says.

    A group is a scope rather than another term on purpose: under `OR` a term
    would quietly pull in entities from outside the group.
    """

    group_id: int | None = None
    tag: str | None = None
    text: str = ''


@dataclass(frozen=True)
class Query:
    where: str
    params: tuple = ()
    source: str = 'entities e'          # the spell view scans `spells s` instead

    def select(self, columns: str = 'e.id, e.entity_key, e.name, e.display_name,'
                                    ' e.source_file',
               order: str = 'e.name, e.source_file, e.entity_path') -> tuple[str, tuple]:
        return (f'SELECT {columns} FROM {self.source} WHERE {self.where}'
                f' ORDER BY {order}'), self.params

    def count(self) -> tuple[str, tuple]:
        return f'SELECT count(*) FROM {self.source} WHERE {self.where}', self.params


def problem(item: Filter, catalog: Catalog) -> str | None:
    """Why this filter cannot run yet, in words, or None when it can.

    The UI calls this on every keystroke: a half-typed component name is not an
    error, it is a row that does not contribute to the query yet.
    """
    if not item.component_type:
        return 'no component type'
    component = catalog.get(item.component_type)
    if component is None:
        return f'unknown component {item.component_type}'
    if item.attribute is None or item.attribute == '':
        return None
    if catalog.attribute(item.component_type, item.attribute) is None:
        return f'{item.component_type} has no attribute {item.attribute}'
    if item.op not in OPERATORS:
        return f'unknown operator {item.op}'
    if item.value == '':
        return 'no value'
    return None


def entity_query(filters: list[Filter], combinator: str = 'AND',
                 scope: Scope = Scope(), catalog: Catalog | None = None) -> Query:
    catalog = catalog if catalog is not None else Catalog()
    for item in filters:
        failure = problem(item, catalog)
        if failure is not None:
            raise FilterError(failure)

    combinator = combinator.upper()
    if combinator not in COMBINATORS:
        raise FilterError(f'unknown combinator {combinator}')

    bindings = _bindings(filters)
    params: list = []
    if combinator == 'AND':
        # Bindings on one type have to hold at once, so they share an EXISTS
        # and require different rows. Different types just AND.
        terms = []
        for component_type, groups in _by_type(bindings).items():
            sql, values = _exists(component_type, groups, catalog)
            terms.append(sql)
            params += values
        clause = ' AND '.join(terms)
    else:
        # Each binding is independent here, so no self-join. EXISTS is 0 or 1,
        # and summing keeps XOR meaning "exactly one" with three filters.
        terms = []
        for (component_type, _), group in bindings.items():
            sql, values = _exists(component_type, [group], catalog)
            terms.append(f'({sql})')
            params += values
        if len(terms) <= 1:
            clause = terms[0] if terms else ''
        else:
            total = ' + '.join(terms)
            clause = f'(({total}) >= 1)' if combinator == 'OR' else f'(({total}) = 1)'

    conditions = [clause] if clause else []
    conditions += _scope(scope, params)
    return Query(' AND '.join(conditions) if conditions else '1', tuple(params))


# -- bindings --------------------------------------------------------------


def _bindings(filters: list[Filter]) -> dict[tuple[str, int], list[Filter]]:
    grouped: dict[tuple[str, int], list[Filter]] = {}
    for item in filters:
        grouped.setdefault((item.component_type, item.binding), []).append(item)
    return grouped


def _by_type(bindings: dict[tuple[str, int], list[Filter]]) \
        -> dict[str, list[list[Filter]]]:
    grouped: dict[str, list[list[Filter]]] = {}
    for (component_type, _), group in bindings.items():
        grouped.setdefault(component_type, []).append(group)
    return grouped


@dataclass
class _Alias:
    source: str          # "SpriteComponent" b0
    identity: str        # b0.component_id — comparable across alias kinds
    conditions: list[str] = field(default_factory=list)
    params: list = field(default_factory=list)


def _exists(component_type: str, groups: list[list[Filter]],
            catalog: Catalog) -> tuple[str, list]:
    aliases = [_alias(component_type, group, catalog, f'b{index}')
               for index, group in enumerate(groups)]
    conditions = [condition for alias in aliases for condition in alias.conditions]
    # `components.id` is the `component_id` of the typed row, so the two alias
    # kinds compare cleanly even when one binding is a bare "has component".
    for left in range(len(aliases)):
        for right in range(left + 1, len(aliases)):
            conditions.append(f'{aliases[left].identity} <> {aliases[right].identity}')
    params = [value for alias in aliases for value in alias.params]
    sources = ', '.join(alias.source for alias in aliases)
    return (f'EXISTS (SELECT 1 FROM {sources} WHERE {" AND ".join(conditions)})',
            params)


def _alias(component_type: str, group: list[Filter], catalog: Catalog,
           name: str) -> _Alias:
    predicates = [item for item in group if item.attribute]
    component = catalog.get(component_type)
    if not predicates or component is None or not component.has_table:
        # "Has this component", through the index rather than the typed table,
        # so it also works for a type a future patch leaves undocumented.
        return _Alias(f'components {name}', f'{name}.id',
                      [f'{name}.entity_id = e.id', f'{name}.component_type = ?'],
                      [component_type])

    alias = _Alias(f'{quote(component_type)} {name}', f'{name}.component_id',
                   [f'{name}.entity_id = e.id'])
    for item in predicates:
        attribute = catalog.attribute(component_type, item.attribute)
        sql, params = _predicate(name, item, attribute.numeric)
        alias.conditions.append(sql)
        alias.params += params
    return alias


def _predicate(alias: str, item: Filter, numeric: bool) -> tuple[str, list]:
    column = f'{alias}.{quote(item.attribute)}'
    if item.op == 'contains':
        return (f'CAST({column} AS TEXT) LIKE ? {LIKE_ESCAPE_SQL}',
                [f'%{like_escape(item.value)}%'])
    value = coerce(item.value, numeric)
    if item.op == '!=':
        # `col != ?` is NULL, and therefore false, for a NULL column. An
        # undocumented attribute left NULL is still "not 5".
        return f'{column} IS NOT ?', [value]
    return f'{column} {item.op} ?', [value]


def coerce(value: str, numeric: bool) -> object:
    """Values arrive as text from an Input. A numeric column compared against
    a string matches nothing, so cast when the column is numeric and the text
    looks like a number — and leave it alone when it does not, because 2180
    attributes are INTEGER columns holding a material name."""
    if not numeric:
        return value
    text = value.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return value


def like_escape(value: str) -> str:
    for character in (LIKE_ESCAPE, '%', '_'):
        value = value.replace(character, LIKE_ESCAPE + character)
    return value


# -- scopes ----------------------------------------------------------------


def _scope(scope: Scope, params: list) -> list[str]:
    conditions = []
    if scope.group_id is not None:
        conditions.append('e.entity_key IN (SELECT entity_key FROM'
                          ' entity_group_members WHERE group_id = ?)')
        params.append(scope.group_id)
    if scope.tag:
        conditions.append('EXISTS (SELECT 1 FROM entity_tags g'
                          ' WHERE g.entity_id = e.id AND g.tag = ?)')
        params.append(scope.tag)
    if scope.text:
        pattern = f'%{like_escape(scope.text)}%'
        conditions.append(
            f'(e.name LIKE ? {LIKE_ESCAPE_SQL}'
            f' OR e.display_name LIKE ? {LIKE_ESCAPE_SQL}'
            f' OR e.source_file LIKE ? {LIKE_ESCAPE_SQL})')
        params += [pattern, pattern, pattern]
    return conditions

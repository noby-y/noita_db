"""Spells, read out of `data/scripts/gun/gun_actions.lua`.

The file is Lua source, not data, so it is parsed into an AST (`luaparser`)
rather than scraped. That matters: 69 of the 491 entries in the file sit inside
`--[[ ]]--` blocks and a regex pass would happily ingest them as real spells,
and 69 line comments contain an unbalanced `"` that desyncs a naive quote
scanner.

Each entry in the global `actions` table becomes a spell. Its literal keys
(`mana`, `price`, `spawn_probability`, ...) become columns; its `action`
function body is mined for three things:

- every write to `c.*`, `shot_effects.*` or a loose global such as
  `current_reload_time`, decomposed into (key, op, value) so that
  `c.screenshake = c.screenshake + 10.0` is findable as `screenshake add 10`;
- every `.xml` passed to an `add_projectile*` call;
- every `.xml` appended to `c.extra_entities`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from luaparser import ast
from luaparser import astnodes as nodes

ACTIONS_TABLE = 'actions'

# Keys whose value is a list of entity paths; they get their own link tables.
LIST_KEYS = ('related_projectiles', 'related_extra_entities')

PROJECTILE_CALLS = {
    'add_projectile': ('path',),
    'add_projectile_trigger_timer': ('path', 'delay_frames', 'count'),
    'add_projectile_trigger_hit_world': ('path', 'count'),
    'add_projectile_trigger_death': ('path', 'count'),
}

# Tables that hold shot configuration. `c` is a ConfigGunActionInfo.
TRACKED_TABLES = ('c', 'shot_effects')

# There is no `sub`: a subtraction is stored as an `add` of a negative number,
# so that one `value_num < 0` filter finds every write that lowers a key.
SET, ADD, MUL, CONCAT, MIN, MAX, EXPR = (
    'set', 'add', 'mul', 'concat', 'min', 'max', 'expr')


@dataclass
class Effect:
    scope: str            # 'c', 'shot_effects', or 'global'
    key: str
    op: str
    value_num: float | None
    value_text: str | None
    conditional: bool     # written inside an if/loop, so not always applied


@dataclass
class ProjectileLink:
    kind: str             # the add_projectile* function that referenced it
    entity_file: str | None
    draw_count: int | None
    delay_frames: int | None
    expression: str       # the argument as written, for unresolved cases


@dataclass
class Spell:
    action_id: str
    properties: dict[str, object]           # literal keys off the entry table
    related_projectiles: list[tuple[str, int]]
    related_extra_entities: list[str]
    effects: list[Effect] = field(default_factory=list)
    projectiles: list[ProjectileLink] = field(default_factory=list)
    extra_entities: list[str] = field(default_factory=list)


def load(lua_path: Path) -> list[Spell]:
    tree = ast.parse(lua_path.read_text(encoding='utf-8'))
    table = _find_actions_table(tree)
    if table is None:
        raise ValueError(f'{lua_path}: no `{ACTIONS_TABLE}` table found')
    return [_read_entry(field.value) for field in table.fields
            if isinstance(field.value, nodes.Table)]


def _find_actions_table(tree) -> nodes.Table | None:
    for statement in ast.walk(tree):
        if not isinstance(statement, (nodes.Assign, nodes.LocalAssign)):
            continue
        for target, value in zip(statement.targets, statement.values):
            if _name_of(target) == ACTIONS_TABLE and isinstance(value, nodes.Table):
                return value
    return None


def _read_entry(entry: nodes.Table) -> Spell:
    properties: dict[str, object] = {}
    related_projectiles: list[tuple[str, int]] = []
    related_extra_entities: list[str] = []
    action = None

    for field_node in entry.fields:
        key = _name_of(field_node.key)
        if key is None:
            continue
        value = field_node.value
        if key == 'action':
            action = value
        elif key == 'related_projectiles':
            related_projectiles = _read_projectile_list(value)
        elif key == 'related_extra_entities':
            related_extra_entities = [p for p in _read_string_list(value)]
        else:
            literal = _property_value(value)
            if literal is not None:
                properties[key] = literal

    spell = Spell(
        action_id=str(properties.get('id', '')),
        properties=properties,
        related_projectiles=related_projectiles,
        related_extra_entities=related_extra_entities,
    )
    if isinstance(action, (nodes.AnonymousFunction, nodes.Function)):
        _read_action(action, spell)
    return spell


# -- action body -----------------------------------------------------------


def _read_action(action, spell: Spell) -> None:
    locals_seen = {_name_of(arg) for arg in getattr(action, 'args', []) if _name_of(arg)}
    _walk_block(action.body, spell, locals_seen, conditional=False)


def _walk_block(block, spell: Spell, locals_seen: set[str], conditional: bool) -> None:
    statements = block.body if isinstance(block, nodes.Block) else block
    for statement in statements:
        _walk_statement(statement, spell, locals_seen, conditional)


def _walk_statement(statement, spell: Spell, locals_seen: set[str], conditional: bool) -> None:
    if isinstance(statement, nodes.LocalAssign):
        for target in statement.targets:
            name = _name_of(target)
            if name:
                locals_seen.add(name)
        for value in statement.values:
            _walk_calls(value, spell)
        return

    if isinstance(statement, nodes.Assign):
        for target, value in zip(statement.targets, _pad(statement.values, statement.targets)):
            effect = _read_assignment(target, value, locals_seen, conditional)
            if effect is not None:
                spell.effects.append(effect)
                if effect.scope == 'c' and effect.key == 'extra_entities':
                    spell.extra_entities.extend(_split_paths(effect.value_text))
            if value is not None:
                _walk_calls(value, spell)
        return

    if isinstance(statement, (nodes.Call, nodes.Invoke)):
        _walk_calls(statement, spell)
        return

    # Anything with a nested block: everything inside is conditionally applied.
    for child_block in _nested_blocks(statement):
        _walk_block(child_block, spell, locals_seen, conditional=True)
    for expression in _nested_expressions(statement):
        _walk_calls(expression, spell)


def _nested_blocks(statement) -> list:
    blocks = []
    for attribute in ('body', 'orelse'):
        value = getattr(statement, attribute, None)
        if isinstance(value, nodes.Block):
            blocks.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, nodes.Block):
                    blocks.append(item)
                elif isinstance(item, (nodes.ElseIf, nodes.If)):
                    blocks.extend(_nested_blocks(item))
    return blocks


def _nested_expressions(statement) -> list:
    out = []
    for attribute in ('test', 'values', 'iter', 'start', 'stop', 'step'):
        value = getattr(statement, attribute, None)
        if isinstance(value, list):
            out.extend(value)
        elif value is not None:
            out.append(value)
    return out


def _walk_calls(node, spell: Spell) -> None:
    for child in ast.walk(node):
        if not isinstance(child, nodes.Call):
            continue
        name = _name_of(child.func)
        signature = PROJECTILE_CALLS.get(name or '')
        if signature is None:
            continue
        arguments = {key: child.args[index] if index < len(child.args) else None
                     for index, key in enumerate(signature)}
        path_node = arguments.get('path')
        path = _literal(path_node) if path_node is not None else None
        spell.projectiles.append(ProjectileLink(
            kind=name,
            entity_file=path if isinstance(path, str) else None,
            draw_count=_int_or_none(arguments.get('count')),
            delay_frames=_int_or_none(arguments.get('delay_frames')),
            expression=_source(path_node) if path_node is not None else '',
        ))


def _read_assignment(target, value, locals_seen: set[str],
                     conditional: bool) -> Effect | None:
    scope, key = _classify_target(target, locals_seen)
    if scope is None or value is None:
        return None
    op, value_num, value_text = _classify_value(target, value)
    return Effect(
        scope=scope, key=key, op=op, value_num=value_num, value_text=value_text,
        conditional=conditional,
    )


def _classify_target(target, locals_seen: set[str]) -> tuple[str | None, str]:
    if isinstance(target, nodes.Index):
        table = _name_of(target.value)
        key = _name_of(target.idx)
        if table in TRACKED_TABLES and key:
            return table, key
        return None, ''
    name = _name_of(target)
    if name and name not in locals_seen:
        # A loose global such as `current_reload_time` or `mana`.
        return 'global', name
    return None, ''


def _classify_value(target, value) -> tuple[str, float | None, str | None]:
    literal = _literal(value)
    if isinstance(literal, bool):
        return SET, float(literal), None
    if isinstance(literal, (int, float)):
        return SET, float(literal), None
    if isinstance(literal, str):
        return SET, None, literal

    if isinstance(value, nodes.Concat):
        text = _literal(value.right)
        if _same_target(value.left, target) and isinstance(text, str):
            return CONCAT, None, text
        return EXPR, None, _source(value)

    # A subtraction becomes an `add` of the negated literal; see the op list.
    arithmetic = {nodes.AddOp: (ADD, 1), nodes.SubOp: (ADD, -1), nodes.MultOp: (MUL, 1)}
    for node_type, (op, sign) in arithmetic.items():
        if isinstance(value, node_type):
            # Accept `c.x = c.x + 5` and chains like
            # `t = t - ACTION_DRAW_RELOAD_TIME_INCREASE - 5`, where the literal
            # is the right operand and the target is somewhere on the left.
            number = _literal(value.right)
            if isinstance(number, (int, float)) and _references(value.left, target):
                return op, sign * float(number), None
            return EXPR, None, _source(value)

    if isinstance(value, nodes.Call):
        name = _source(value.func)
        if name in ('math.min', 'math.max') and len(value.args) == 2:
            other = [a for a in value.args if not _same_target(a, target)]
            number = _literal(other[0]) if len(other) == 1 else None
            if isinstance(number, (int, float)):
                return (MIN if name == 'math.min' else MAX), float(number), None

    return EXPR, None, _source(value)


# -- literals --------------------------------------------------------------


def _literal(node) -> object | None:
    if isinstance(node, nodes.String):
        return node.s.decode('utf-8') if isinstance(node.s, bytes) else node.s
    if isinstance(node, nodes.Number):
        return node.n
    if isinstance(node, nodes.TrueExpr):
        return True
    if isinstance(node, nodes.FalseExpr):
        return False
    if isinstance(node, nodes.UMinusOp):
        inner = _literal(node.operand)
        return -inner if isinstance(inner, (int, float)) and not isinstance(inner, bool) else None
    return None


def _property_value(node) -> object | None:
    """Like `_literal`, but a bare identifier counts as its own name. Entry
    keys use them as enum constants (`type = ACTION_TYPE_PROJECTILE`); inside
    an action body the same node is a variable and must not be read as text."""
    if isinstance(node, nodes.Name):
        return node.id
    return _literal(node)


def _read_string_list(node) -> list[str]:
    if not isinstance(node, nodes.Table):
        return []
    out = []
    for field_node in node.fields:
        value = _literal(field_node.value)
        if isinstance(value, str):
            out.append(value)
    return out


def _read_projectile_list(node) -> list[tuple[str, int]]:
    """`related_projectiles` is `{"path.xml"}` or `{"path.xml", 3}`."""
    if not isinstance(node, nodes.Table):
        return []
    paths: list[tuple[str, int]] = []
    for field_node in node.fields:
        value = _literal(field_node.value)
        if isinstance(value, str):
            paths.append((value, 1))
        elif isinstance(value, (int, float)) and paths:
            path, _ = paths[-1]
            paths[-1] = (path, int(value))
    return paths


def _split_paths(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in text.split(',') if part.strip().endswith('.xml')]


def _name_of(node) -> str | None:
    if isinstance(node, nodes.Name):
        return node.id
    if isinstance(node, nodes.String):
        return node.s.decode('utf-8') if isinstance(node.s, bytes) else node.s
    return None


def _source(node) -> str:
    if node is None:
        return ''
    try:
        return ast.to_lua_source(node).strip()
    except Exception:
        return ''


def _same_target(node, target) -> bool:
    return _source(node) == _source(target)


def _references(node, target) -> bool:
    wanted = _source(target)
    return any(_source(child) == wanted for child in ast.walk(node))


def _int_or_none(node) -> int | None:
    value = _literal(node) if node is not None else None
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pad(values: list, targets: list) -> list:
    return list(values) + [None] * (len(targets) - len(values))

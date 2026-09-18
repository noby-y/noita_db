"""The component schema, read from `comp_docs/`.

Each json file documents one component (or one of the helper config structs a
component embeds, like `ConfigExplosion`). A documented field becomes one or
more *leaves* — the things that actually appear as XML attributes:

    float  damage                 -> damage
    vec2   offset                 -> offset.x, offset.y
    aabb   area_aabb              -> area_aabb.min_x, .min_y, .max_x, .max_y
    Object config_explosion       -> config_explosion.damage, ... (recursive)

A leaf's `path` is exactly the attribute name the XML uses, which is also the
SQLite column name. Fields whose type has no XML representation (raw pointers,
engine-internal handles) produce no leaves; if an entity sets one anyway, it
lands in `unimplemented_attributes`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

INTEGER, REAL, TEXT = 'INTEGER', 'REAL', 'TEXT'

# Scalars, keyed by the raw_type string comp_docs reports.
_SCALARS: dict[str, str] = {
    'bool': INTEGER, 'LensValue<bool>': INTEGER,
    'int': INTEGER, 'int8': INTEGER, 'int16': INTEGER, 'int32': INTEGER,
    'int64': INTEGER, 'uint8': INTEGER, 'uint16': INTEGER, 'uint32': INTEGER,
    'uint32_t': INTEGER, 'uint64': INTEGER, 'unsigned int': INTEGER,
    'LensValue<int>': INTEGER, 'EntityID': INTEGER, 'EntityTypeID': INTEGER,
    'float': REAL, 'double': REAL, 'LensValue<float>': REAL,
    'std::string': TEXT, 'std_string': TEXT, 'USTRING': TEXT,
    'StatusEffectType': TEXT, 'EntityTags': TEXT, 'ComponentTags': TEXT,
}

# Composite value types. Suffixes are the ones Noita's XML actually writes,
# e.g. `offset.x`, `randomize_speed.min`, `area_aabb.min_x`, `color.r`.
_COMPOSITES: dict[str, list[tuple[str, str]]] = {
    'vec2': [('x', REAL), ('y', REAL)],
    'types::vector2': [('x', REAL), ('y', REAL)],
    'b2Vec2': [('x', REAL), ('y', REAL)],
    'ivec2': [('x', INTEGER), ('y', INTEGER)],
    'ValueRange': [('min', REAL), ('max', REAL)],
    'ValueRangeInt': [('min', INTEGER), ('max', INTEGER)],
    'types::aabb': [('min_x', REAL), ('min_y', REAL), ('max_x', REAL), ('max_y', REAL)],
    'types::iaabb': [('min_x', INTEGER), ('min_y', INTEGER), ('max_x', INTEGER), ('max_y', INTEGER)],
    'types::fcolor': [('r', REAL), ('g', REAL), ('b', REAL), ('a', REAL)],
    'types::xform': [
        ('position.x', REAL), ('position.y', REAL), ('rotation', REAL),
        ('scale.x', REAL), ('scale.y', REAL),
    ],
}

# A transform's scale defaults to 1, unlike every other xform member.
_XFORM_DEFAULTS = {'position.x': 0.0, 'position.y': 0.0, 'rotation': 0.0,
                   'scale.x': 1.0, 'scale.y': 1.0}

# Repeating fields. They show up in XML as child tags holding a list of rows
# (`<count_per_material_type><Material material="oil" count="200"/></...>`),
# so they are stored as a JSON array of attribute maps in a TEXT column.
_CONTAINER_RE = re.compile(
    r'^(std::vector|std::set|VEC_|VECTOR_|MAP_|.*ArrayInline$|.*_VEC$|.*_MAP$|'
    r'MATERIAL_VEC_DOUBLES|STATS_KEY_VALUE_MAP|VISITED_VEC|POLYGON_VEC|'
    r'IKLimbStateVec|AI_STATE_STACK|ENTITY_VEC)'
)

# The bare `<_Transform>` tag entities use at top level. It has no comp_docs
# entry but is the same shape as a types::xform.
TRANSFORM_COMPONENT = '_Transform'

# Meta attributes Noita accepts on any component tag.
META_ENABLED = '_enabled'
META_TAGS = '_tags'
META_REMOVE_FROM_BASE = '_remove_from_base'
META_ATTRS = (META_ENABLED, META_TAGS, META_REMOVE_FROM_BASE)


@dataclass(frozen=True)
class Leaf:
    """One storable value: an XML attribute path and its SQLite column."""
    path: str
    sql_type: str
    default: object
    comment: str = ''
    is_container: bool = False
    raw_type: str = ''


@dataclass
class ComponentDoc:
    name: str
    is_component: bool
    leaves: dict[str, Leaf]           # path -> Leaf, in declaration order

    def nested_prefix(self, tag: str) -> str | None:
        """`tag` names a nested value written as a child element (an embedded
        config struct, or a transform) if any leaf lives underneath it."""
        prefix = f'{tag}.'
        return prefix if any(p.startswith(prefix) for p in self.leaves) else None

    def container(self, tag: str) -> Leaf | None:
        leaf = self.leaves.get(tag)
        return leaf if leaf is not None and leaf.is_container else None


class ComponentDocs:
    def __init__(self, docs: dict[str, ComponentDoc]) -> None:
        self.docs = docs

    def get(self, name: str) -> ComponentDoc | None:
        return self.docs.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self.docs

    @property
    def components(self) -> list[ComponentDoc]:
        return [d for d in self.docs.values() if d.is_component]


def load(comp_docs_dir: Path) -> ComponentDocs:
    raw: dict[str, dict] = {}
    for path in sorted(comp_docs_dir.glob('*.json')):
        doc = json.loads(path.read_text(encoding='utf-8'))
        raw[doc['name']] = doc
    if not raw:
        raise FileNotFoundError(f'no component json files under {comp_docs_dir}')

    docs: dict[str, ComponentDoc] = {}
    for name in raw:
        docs[name] = _build(name, raw, stack=())
    docs[TRANSFORM_COMPONENT] = ComponentDoc(
        name=TRANSFORM_COMPONENT,
        is_component=True,
        leaves={path: Leaf(path, sql, _XFORM_DEFAULTS[path])
                for path, sql in _COMPOSITES['types::xform']},
    )
    return ComponentDocs(docs)


def _build(name: str, raw: dict[str, dict], stack: tuple[str, ...]) -> ComponentDoc:
    doc = raw[name]
    leaves: dict[str, Leaf] = {}
    for field in doc['fields']:
        _expand(field, '', raw, stack + (name,), leaves)
    return ComponentDoc(
        name=name,
        # `Component`-derived types are attachable; `ceng::IConfigBase` ones are
        # embedded structs and never appear as a top-level tag.
        is_component=doc.get('base') == 'Component',
        leaves=leaves,
    )


def _expand(field: dict, prefix: str, raw: dict[str, dict], stack: tuple[str, ...],
            leaves: dict[str, Leaf]) -> None:
    raw_type = field['raw_type']
    name = field['name']
    path = f'{prefix}{name}'
    comment = field.get('comment', '')
    default_text = field.get('default_value', '')

    if raw_type in _SCALARS:
        sql = _SCALARS[raw_type]
        leaves[path] = Leaf(path, sql, parse_default(default_text, sql, raw_type),
                            comment, raw_type=raw_type)
        return

    if raw_type.endswith('::Enum'):
        leaves[path] = Leaf(path, TEXT, default_text or None, comment, raw_type=raw_type)
        return

    if raw_type in _COMPOSITES:
        values = _parse_composite_default(raw_type, default_text)
        for suffix, sql in _COMPOSITES[raw_type]:
            leaf_path = f'{path}.{suffix}'
            leaves[leaf_path] = Leaf(leaf_path, sql, values.get(suffix), comment,
                                     raw_type=raw_type)
        return

    if raw_type in raw:  # an embedded config struct
        if raw_type in stack:  # defensive: comp_docs has no cycles today
            return
        for sub in raw[raw_type]['fields']:
            _expand(sub, f'{path}.', raw, stack + (raw_type,), leaves)
        return

    if _CONTAINER_RE.match(raw_type):
        leaves[path] = Leaf(path, TEXT, None, comment, is_container=True,
                            raw_type=raw_type)
        return

    # Pointers and engine-internal state: nothing an XML file can set.


def parse_default(text: str, sql_type: str, raw_type: str = '') -> object:
    """Turn a comp_docs `default_value` string into a Python value."""
    text = (text or '').strip()
    if raw_type.startswith('LensValue'):
        # "[1.000000, 0.000000: -1]" - the first element is the base value.
        inner = text.strip('[]').split(',')[0].strip()
        text = inner
    if text == '':
        return '' if sql_type == TEXT else None
    if sql_type == TEXT:
        return text
    if sql_type == INTEGER:
        return parse_int(text)
    return parse_float(text)


def parse_int(text: str) -> int | None:
    text = text.strip()
    if text in ('true', 'True'):
        return 1
    if text in ('false', 'False'):
        return 0
    try:
        return int(text, 0) if text.lower().startswith('0x') else int(text)
    except ValueError:
        pass
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_float(text: str) -> float | None:
    text = text.strip()
    if text in ('true', 'True'):
        return 1.0
    if text in ('false', 'False'):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None


def _parse_composite_default(raw_type: str, text: str) -> dict[str, object]:
    """Read the printed form of a composite default, e.g. "(0, 0)", "[0, 0.2]"."""
    if raw_type == 'types::xform':
        return dict(_XFORM_DEFAULTS)
    numbers = [n for n in re.findall(r'-?\d+(?:\.\d+)?(?:e-?\d+)?', text)]
    suffixes = _COMPOSITES[raw_type]
    if raw_type == 'types::aabb' or raw_type == 'types::iaabb':
        # printed as "(min_x, min_y) (max_x, max_y)"
        order = ['min_x', 'min_y', 'max_x', 'max_y']
        numbers = numbers[:4]
        values = dict(zip(order, numbers))
    else:
        values = {suffix: value for (suffix, _), value in zip(suffixes, numbers)}
    out: dict[str, object] = {}
    for suffix, sql in suffixes:
        text_value = values.get(suffix)
        if text_value is None:
            out[suffix] = None
        else:
            out[suffix] = parse_int(text_value) if sql == INTEGER else parse_float(text_value)
    return out


#: uint32 fields hold packed colours, written in XML as bare hex ("ff20f050").
_UNSIGNED_TYPES = ('uint32', 'uint32_t')
_BARE_HEX_RE = re.compile(r'^[0-9a-fA-F]{6}$|^[0-9a-fA-F]{8}$')


def coerce(value: str, leaf: Leaf) -> tuple[object, bool]:
    """Convert an XML attribute string for storage, returning
    `(value, converted)`.

    `converted` is False when the text does not fit the documented type. That
    happens on purpose all over `data/`: dozens of fields are ints at runtime
    but name a material or a herd in XML (`material="wood_prop"`), because the
    engine resolves the name to an id on load. The text is kept as-is — SQLite
    columns carry affinity, not a hard type — so nothing is lost, and the
    caller records a warning."""
    if leaf.sql_type == TEXT:
        return value, True
    if leaf.sql_type == INTEGER:
        number = parse_int(value)
        if number is None and leaf.raw_type in _UNSIGNED_TYPES and _BARE_HEX_RE.match(value.strip()):
            number = int(value.strip(), 16)
        if number is not None:
            return number, True
    else:
        number = parse_float(value)
        if number is not None:
            return number, True
    return value, False

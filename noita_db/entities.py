"""Entity discovery and `<Base>` resolution.

An entity file is any XML under the root whose root tag is `Entity`. Resolving
one produces its final, flat component list: bases merged in, attribute
defaults still to be filled by the writer.

The merge rules, in the order they are applied to the children of `<Entity>`:

1. `<Base file="...">` resolves the referenced file first, recursively.
2. A component tag inside the `<Base>` overwrites attributes on the inherited
   component of the same type. Attributes it does not mention keep the
   inherited value.
3. If the base has no component of that type, the tag does nothing — it never
   adds one. When the base has several of a type, the k-th override tag
   matches the k-th inherited instance (`mimic_potion.xml` removes two
   distinct inherited LuaComponents this way).
4. A component tag outside `<Base>` always appends a new instance.
5. A `<Base>` pointing at a file that does not exist is skipped entirely.

`_remove_from_base="1"` on an override deletes the matched instance instead of
merging into it. That attribute is not in the plan's rule list but Noita
honours it, and two entities depend on it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from . import comp_docs
from .comp_docs import ComponentDocs
from .xml_tolerant import Element, parse_file

ENTITY_TAG = 'Entity'
BASE_TAG = 'Base'


@dataclass
class ComponentInstance:
    type: str
    #  Flattened attribute paths in document order. Duplicates are kept: the
    #  first is the real value, later ones become `name#1`, `name#2`.
    attrs: list[tuple[str, str]] = field(default_factory=list)
    #  Repeating child rows, e.g. count_per_material_type -> [{material, count}]
    containers: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def set_attr(self, path: str, value: str) -> None:
        for index, (existing, _) in enumerate(self.attrs):
            if existing == path:
                self.attrs[index] = (path, value)
                return
        self.attrs.append((path, value))

    def first(self, path: str) -> str | None:
        for existing, value in self.attrs:
            if existing == path:
                return value
        return None


@dataclass
class BaseRef:
    file: str
    found: bool


@dataclass
class ResolvedEntity:
    source_file: str                 # the file this element was literally written in;
                                     # an inherited child entity keeps its base's path
    name_key: str | None             # the raw `name` attribute, `$`-prefix included
    name: str | None                 # name attribute; None means "derive from the file"
    tags: list[str]
    components: list[ComponentInstance] = field(default_factory=list)
    children: list["ResolvedEntity"] = field(default_factory=list)
    bases: list[BaseRef] = field(default_factory=list)
    serialize: str | None = None


class EntityResolver:
    def __init__(self, root: Path, docs: ComponentDocs) -> None:
        self.root = root
        self.docs = docs
        self.warnings: list[tuple[str, str]] = []   # (source_file, message)
        self._cache: dict[str, ResolvedEntity | None] = {}
        self._in_progress: set[str] = set()

    # -- discovery ---------------------------------------------------------

    def find_entity_files(self) -> list[str]:
        """Every XML under the root whose root tag is `Entity`, by root tag and
        not by directory, as the plan requires."""
        found = []
        for path in sorted(self.root.rglob('*.xml')):
            try:
                element = parse_file(path)
            except Exception as error:  # unreadable or empty
                self.warnings.append((_rel(path, self.root), f'unparseable: {error}'))
                continue
            if element.tag == ENTITY_TAG:
                found.append(_rel(path, self.root))
        return found

    # -- resolution --------------------------------------------------------

    def resolve(self, rel_path: str) -> ResolvedEntity | None:
        """Resolve a file's root entity, or None if the file is missing or is
        not an entity. Results are cached and must be copied before mutation."""
        if rel_path in self._cache:
            return self._cache[rel_path]
        if rel_path in self._in_progress:
            self.warnings.append((rel_path, 'base inheritance cycle, base ignored'))
            return None

        full = self.root / rel_path
        if not full.is_file():
            self._cache[rel_path] = None
            return None
        try:
            element = parse_file(full)
        except Exception as error:
            self.warnings.append((rel_path, f'unparseable: {error}'))
            self._cache[rel_path] = None
            return None
        if element.tag != ENTITY_TAG:
            self._cache[rel_path] = None
            return None

        self._in_progress.add(rel_path)
        try:
            resolved = self._build(element, rel_path, is_root=True)
        finally:
            self._in_progress.discard(rel_path)
        self._cache[rel_path] = resolved
        return resolved

    def _build(self, element: Element, source_file: str, is_root: bool) -> ResolvedEntity:
        name_key = element.get('name')
        resolved = ResolvedEntity(
            source_file=source_file,
            name_key=name_key,
            # Rule: an entity with no `name` is named after its file.
            name=name_key or (Path(source_file).stem if is_root else None),
            # Only the first `tags` attribute counts if the tag sets it twice.
            tags=split_tags(element.get('tags')),
            serialize=element.get('serialize'),
        )

        own_tags = resolved.tags
        inherited_tags: list[str] = []

        for child in element.children:
            if child.tag == BASE_TAG:
                inherited_tags.extend(self._apply_base(child, resolved, source_file))
            elif child.tag == ENTITY_TAG:
                resolved.children.append(self._build(child, source_file, is_root=False))
            else:
                resolved.components.append(self._flatten(child))

        resolved.tags = dedupe(inherited_tags + own_tags)
        return resolved

    def _apply_base(self, base_element: Element, target: ResolvedEntity,
                    source_file: str) -> list[str]:
        """Merge one `<Base>` into `target`, returning the tags it contributes."""
        base_file = base_element.get('file')
        if not base_file:
            return []
        base = self.resolve(base_file)
        target.bases.append(BaseRef(file=base_file, found=base is not None))
        if base is None:
            return []   # rule 5: a missing base is parsed as if absent

        inherited = copy.deepcopy(base.components)
        inherited_children = copy.deepcopy(base.children)
        # Snapshot the per-type ordering up front: an override that removes an
        # instance must not shift the position the next override matches.
        by_type: dict[str, list[ComponentInstance]] = {}
        for instance in inherited:
            by_type.setdefault(instance.type, []).append(instance)
        seen_by_type: dict[str, int] = {}

        for override in base_element.children:
            if override.tag == ENTITY_TAG:
                target.children.append(self._build(override, source_file, is_root=False))
                continue

            index = seen_by_type.get(override.tag, 0)
            seen_by_type[override.tag] = index + 1
            matches = by_type.get(override.tag, ())
            if index >= len(matches):
                continue                      # rule 3: overrides never add
            instance = matches[index]

            patch = self._flatten(override)
            if _is_true(patch.first(comp_docs.META_REMOVE_FROM_BASE)):
                # By identity: two inherited instances of a type can hold
                # identical attributes and would compare equal.
                inherited[:] = [c for c in inherited if c is not instance]
                continue
            for path, value in patch.attrs:
                if path == comp_docs.META_REMOVE_FROM_BASE:
                    continue
                instance.set_attr(path, value)
            instance.containers.update(patch.containers)

        target.components.extend(inherited)
        target.children.extend(inherited_children)
        return list(base.tags)

    # -- component flattening ---------------------------------------------

    def _flatten(self, element: Element) -> ComponentInstance:
        instance = ComponentInstance(type=element.tag)
        self._flatten_into(element, self.docs.get(element.tag), '', instance)
        return instance

    def _flatten_into(self, element: Element, doc, prefix: str,
                      instance: ComponentInstance) -> None:
        for name, value in element.attrs:
            instance.attrs.append((f'{prefix}{name}', value))
        for child in element.children:
            local = child.tag if not prefix else f'{prefix}{child.tag}'
            container = doc.container(local) if doc is not None else None
            if container is not None:
                instance.containers[local] = [dict(row.attrs) for row in child.children]
                continue
            nested = doc.nested_prefix(local) if doc is not None else None
            if nested is not None:
                self._flatten_into(child, doc, nested, instance)
                continue
            # Not part of the documented shape; keep it so it surfaces in
            # unimplemented_attributes rather than vanishing.
            self._flatten_into(child, doc, f'{local}.', instance)


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [tag.strip() for tag in value.split(',') if tag.strip()]


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _is_true(value: str | None) -> bool:
    return value is not None and value.strip().lower() in ('1', 'true')


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()

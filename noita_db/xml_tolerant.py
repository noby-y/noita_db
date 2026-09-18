"""A forgiving XML reader for Noita's entity files.

Noita's own loader accepts a few things a conforming XML parser rejects, and
`data/` makes use of all of them:

- the same attribute set twice on one tag (``duplicate attribute``)
- ``<!-- ... -->`` sitting *between* attributes, inside an open tag
- ``--`` inside comment text (``<!-- art - main body ------ -->``)
- a file that ends without closing its root (``data/entities/misc/eradicate.xml``)

So the entity pipeline tokenizes the files itself. Attributes are kept as an
ordered list rather than a dict, because duplicates are meaningful: the parser
stores the first in the typed column and the rest as ``name#1``, ``name#2``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_NAME_STOP = frozenset('=/><\t\n\r ')


@dataclass
class Element:
    tag: str
    attrs: list[tuple[str, str]] = field(default_factory=list)
    children: list["Element"] = field(default_factory=list)
    line: int = 0

    def get(self, name: str, default: str | None = None) -> str | None:
        """First value set for `name`, mirroring how Noita reads a tag."""
        for key, value in self.attrs:
            if key == name:
                return value
        return default

    def iter_attrs(self, name: str):
        for key, value in self.attrs:
            if key == name:
                yield value


class XmlError(Exception):
    pass


class _Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.n = len(text)

    def line_of(self, pos: int) -> int:
        return self.text.count('\n', 0, pos) + 1

    def skip_ws(self) -> None:
        while self.pos < self.n and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def try_skip_comment(self) -> bool:
        """Skip ``<!-- ... -->`` / ``<? ... ?>`` / ``<!DOCTYPE ...>``."""
        if not self.text.startswith('<', self.pos):
            return False
        if self.text.startswith('<!--', self.pos):
            end = self.text.find('-->', self.pos + 4)
            self.pos = self.n if end < 0 else end + 3
            return True
        if self.text.startswith('<?', self.pos) or self.text.startswith('<!', self.pos):
            end = self.text.find('>', self.pos)
            self.pos = self.n if end < 0 else end + 1
            return True
        return False

    def read_name(self) -> str:
        start = self.pos
        while self.pos < self.n and self.text[self.pos] not in _NAME_STOP:
            self.pos += 1
        return self.text[start:self.pos]


def parse_string(text: str, source: str = '<string>') -> Element:
    """Parse `text` into an element tree, returning the first root element.

    Unclosed tags are closed at end of input; a closing tag with no matching
    open tag on the stack is dropped.
    """
    reader = _Reader(text)
    roots: list[Element] = []
    stack: list[Element] = []

    while reader.pos < reader.n:
        lt = reader.text.find('<', reader.pos)
        if lt < 0:
            break
        reader.pos = lt
        if reader.try_skip_comment():
            continue

        if reader.text.startswith('</', reader.pos):
            reader.pos += 2
            name = reader.read_name()
            end = reader.text.find('>', reader.pos)
            reader.pos = reader.n if end < 0 else end + 1
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth].tag == name:
                    del stack[depth:]
                    break
            continue

        reader.pos += 1
        line = reader.line_of(reader.pos)
        name = reader.read_name()
        if not name:  # a stray '<' in text content
            continue
        element = Element(tag=name, line=line)
        self_closing = _read_attrs(reader, element)

        if stack:
            stack[-1].children.append(element)
        else:
            roots.append(element)
        if not self_closing:
            stack.append(element)

    if not roots:
        raise XmlError(f'{source}: no elements found')
    return roots[0]


def _read_attrs(reader: _Reader, element: Element) -> bool:
    """Consume a tag's attributes. Returns True if the tag self-closes."""
    while reader.pos < reader.n:
        reader.skip_ws()
        if reader.pos >= reader.n:
            return True
        char = reader.text[reader.pos]
        if char == '>':
            reader.pos += 1
            return False
        if char == '/':
            reader.pos += 1
            if reader.pos < reader.n and reader.text[reader.pos] == '>':
                reader.pos += 1
            return True
        if char == '<':
            # A comment wedged between attributes. Anything else here means the
            # tag was never closed, so treat the '<' as the start of the next tag.
            if reader.try_skip_comment():
                continue
            return True
        if char == '=':  # '=' with no name in front of it
            reader.pos += 1
            continue

        name = reader.read_name()
        if not name:
            reader.pos += 1
            continue
        reader.skip_ws()
        if reader.pos >= reader.n or reader.text[reader.pos] != '=':
            element.attrs.append((name, ''))  # valueless attribute
            continue
        reader.pos += 1
        reader.skip_ws()
        element.attrs.append((name, _read_value(reader)))
    return True


def _read_value(reader: _Reader) -> str:
    if reader.pos >= reader.n:
        return ''
    quote = reader.text[reader.pos]
    if quote in '"\'':
        reader.pos += 1
        end = reader.text.find(quote, reader.pos)
        if end < 0:
            value = reader.text[reader.pos:]
            reader.pos = reader.n
            return value
        value = reader.text[reader.pos:end]
        reader.pos = end + 1
        return value
    start = reader.pos  # unquoted value
    while reader.pos < reader.n and reader.text[reader.pos] not in ' \t\r\n><':
        reader.pos += 1
    return reader.text[start:reader.pos]


def parse_file(path: Path) -> Element:
    return parse_string(path.read_text(encoding='utf-8', errors='replace'), str(path))

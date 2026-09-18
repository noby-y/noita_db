"""One spell filter, as a row of fields: source, key, operator, value.

The source is what the row asks about — the spells row itself, one of the
effect scopes, the raw `spell_effects` table, or one of the entity link tables
— and it decides what the key field completes over. No instance binding: each
row is its own `EXISTS`, because a spell's writes accumulate rather than
compete.
"""

from __future__ import annotations

from typing import Protocol

from textual.app import ComposeResult
from textual.widgets import Button

from .filter_row import FieldRow
from .query import OPERATORS
from .spell_query import SpellFilter
from .widgets import CompletionInput


class SpellSources(Protocol):
    def spell_sources(self) -> list[str]: ...
    def spell_keys(self, source: str) -> list[str]: ...
    def spell_values(self, source: str, key: str) -> list[str]: ...


class SpellFilterRow(FieldRow):
    FIELDS = ('source', 'key', 'op', 'value')

    DEFAULT_CSS = """
    SpellFilterRow { height: 3; width: 1fr; }
    SpellFilterRow.invalid Input#source,
    SpellFilterRow.invalid Input#key { border: tall $error; }
    SpellFilterRow Input#source { width: 18; }
    SpellFilterRow Input#key { width: 30; }
    SpellFilterRow Input#op { width: 14; }
    SpellFilterRow Input#value { width: 1fr; min-width: 12; }
    SpellFilterRow Button { width: 5; min-width: 5; }
    """

    def __init__(self, sources: SpellSources) -> None:
        super().__init__()
        self.sources = sources

    def compose(self) -> ComposeResult:
        yield CompletionInput(self.sources.spell_sources, id='source',
                              placeholder='source',
                              tooltip='spell = a property of the spell; c, '
                                      'shot_effects, global = a write in its '
                                      'action body; effect = the raw table; '
                                      'projectile, extra_entity, related = the '
                                      'entities it spawns')
        yield CompletionInput(self._keys, id='key', placeholder='key')
        yield CompletionInput(lambda: list(OPERATORS), value='=', id='op')
        yield CompletionInput(self._values, id='value', placeholder='value',
                              tooltip='leave blank to match every spell where '
                                      'this is set at all')
        yield Button('✕', id='remove', variant='error')

    @property
    def filter(self) -> SpellFilter:
        key = self._text('key')
        return SpellFilter(source=self._text('source'),
                           key=key or None,
                           op=self._text('op') or '=',
                           value=self._field('value').value)

    def _keys(self) -> list[str]:
        return self.sources.spell_keys(self._text('source'))

    def _values(self) -> list[str]:
        source, key = self._text('source'), self._text('key')
        return self.sources.spell_values(source, key) if source and key else []

"""One filter, as a row of fields.

`FieldRow` is the part both views share: a row of completing inputs and a
remove button, which reports what it currently holds and never refuses input.
Whether that adds up to a filter the app can run is the app's problem — a
half-typed component name is not an error, it is a row that does not run yet.
"""

from __future__ import annotations

from typing import Protocol

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Input

from .query import OPERATORS, Filter
from .widgets import CompletionInput


class Sources(Protocol):
    """What a row needs from the open database to complete a field."""

    def component_types(self) -> list[str]: ...
    def attributes(self, component_type: str) -> list[str]: ...
    def all_attributes(self) -> list[str]: ...
    def values(self, component_type: str, attribute: str) -> list[str]: ...
    def owners(self, attribute: str) -> list[str]: ...


class FieldRow(Horizontal):
    FIELDS: tuple[str, ...] = ()

    class Edited(Message):
        def __init__(self, row: 'FieldRow') -> None:
            super().__init__()
            self.row = row

    class Removed(Message):
        def __init__(self, row: 'FieldRow') -> None:
            super().__init__()
            self.row = row

    def _field(self, name: str) -> Input:
        return self.query_one(f'#{name}', Input)

    def _text(self, name: str) -> str:
        return self._field(name).value.strip()

    @property
    def ready(self) -> bool:
        """False between `mount()` and the row composing all of its fields."""
        return len(self.query(Input)) == len(self.FIELDS)

    @property
    def blank(self) -> bool:
        return not any(self._text(name) for name in self.FIELDS
                       if name not in ('binding', 'op'))

    def on_input_changed(self, event: Input.Changed) -> None:
        self.post_message(self.Edited(self))
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Removed(self))


class FilterRow(FieldRow):
    """Component type, instance binding, attribute, operator, value."""

    FIELDS = ('type', 'binding', 'attribute', 'op', 'value')

    DEFAULT_CSS = """
    FilterRow { height: 3; width: 1fr; }
    FilterRow.invalid Input#type, FilterRow.invalid Input#attribute { border: tall $error; }
    FilterRow Input#type { width: 32; }
    FilterRow Input#binding { width: 7; }
    FilterRow Input#attribute { width: 34; }
    FilterRow Input#op { width: 14; }
    FilterRow Input#value { width: 1fr; min-width: 12; }
    FilterRow Button { width: 5; min-width: 5; }
    """

    def __init__(self, sources: Sources, binding: int = 1) -> None:
        super().__init__()
        self.sources = sources
        self.binding = binding

    def compose(self) -> ComposeResult:
        yield CompletionInput(self.sources.component_types, id='type',
                              placeholder='component')
        yield Input(value=str(self.binding), id='binding', type='integer',
                    tooltip='instance binding: filters sharing a number must be '
                            'the same component instance, different numbers must '
                            'be different instances')
        yield CompletionInput(self._attributes, id='attribute',
                              placeholder='attribute',
                              tooltip='leave blank to match any entity carrying '
                                      'the component')
        yield CompletionInput(lambda: list(OPERATORS), value='=', id='op')
        yield CompletionInput(self._values, id='value', placeholder='value',
                              tooltip='a quoted value is matched literally, so '
                                      '"" asks for an attribute holding nothing')
        yield Button('✕', id='remove', variant='error')

    @property
    def filter(self) -> Filter:
        try:
            binding = int(self._field('binding').value)
        except ValueError:
            binding = 1
        attribute = self._text('attribute')
        return Filter(component_type=self._text('type'),
                      attribute=attribute or None,
                      op=self._text('op') or '=',
                      value=self._field('value').value,
                      binding=max(binding, 1))

    # -- candidates --------------------------------------------------------

    def _attributes(self) -> list[str]:
        component_type = self._text('type')
        # With no component chosen yet, offer every attribute in the database:
        # someone who knows they want `damage` does not necessarily know which
        # component carries it.
        return (self.sources.attributes(component_type) if component_type
                else self.sources.all_attributes())

    def _values(self) -> list[str]:
        component_type, attribute = self._text('type'), self._text('attribute')
        if not component_type or not attribute:
            return []
        return self.sources.values(component_type, attribute)

    # -- events ------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == 'attribute':
            self._adopt_owner(event.value.strip())
        super().on_input_changed(event)

    def _adopt_owner(self, attribute: str) -> None:
        """An attribute that exists on exactly one component type names its own
        component, so fill it in rather than making the user look it up."""
        if attribute and not self._text('type'):
            owners = self.sources.owners(attribute)
            if len(owners) == 1:
                self._field('type').value = owners[0]

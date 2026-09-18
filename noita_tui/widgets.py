"""Small widgets the app builds out of: completion, prompts, confirmation.

Textual has no completing input, so this is one: an `Input` with an
`OptionList` floating under it on its own layer. The candidate list is a
callable rather than a fixed sequence, because the attribute field's options
depend on whatever component type the field beside it currently holds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

MAX_COMPLETIONS = 60


class CompletionList(OptionList):
    DEFAULT_CSS = """
    CompletionList {
        layer: dropdown;
        display: none;
        width: auto;
        max-width: 64;
        height: auto;
        max-height: 12;
        border: round $accent;
        background: $panel;
        padding: 0 1;
    }
    """


class CompletionInput(Input):
    """An Input that offers what the database actually holds.

    Down opens the list and walks it, enter or tab takes the highlighted
    candidate, escape closes it. Everything else is a normal Input, so typing
    a value the list does not know about still works — a half-typed component
    name is not an error, just a filter that does not run yet.
    """

    def __init__(self, candidates: Callable[[], Sequence[str]], **kwargs) -> None:
        super().__init__(**kwargs)
        self.candidates = candidates
        self._list: CompletionList | None = None

    # -- lifetime ----------------------------------------------------------

    def on_mount(self) -> None:
        # The list lives on the screen rather than inside the row: a dropdown
        # parented to a one-line container would be clipped to one line.
        self._list = CompletionList()
        self.screen.mount(self._list)

    def on_unmount(self) -> None:
        if self._list is not None:
            self._list.remove()
            self._list = None

    # -- opening and closing -----------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._list is not None and self._list.display

    def matches(self) -> list[str]:
        query = self.value.strip().lower()
        exact, starts, contains = [], [], []
        for candidate in self.candidates():
            lowered = candidate.lower()
            if query and lowered == query:
                exact.append(candidate)
            elif lowered.startswith(query):
                starts.append(candidate)
            elif query and query in lowered:
                contains.append(candidate)
        # `damage` ahead of `damage_by_type.fire`: the whole word the user
        # typed is more likely what they meant than the first field that
        # happens to begin with it.
        return (exact + starts + contains)[:MAX_COMPLETIONS]

    def refresh_completions(self) -> None:
        if self._list is None:
            return
        options = self.matches()
        # One candidate identical to what is typed is not a suggestion.
        if not options or options == [self.value]:
            self.close()
            return
        self._list.clear_options()
        self._list.add_options([Option(option) for option in options])
        self._list.highlighted = None
        self._list.display = True
        self._place()

    def close(self) -> None:
        if self._list is not None:
            self._list.display = False

    def _place(self) -> None:
        region = self.region
        below = region.y + region.height
        # Flip above the input when there is no room under it.
        height = min(self._list.option_count + 2, 12)
        top = below if below + height <= self.screen.size.height else region.y - height
        self._list.styles.offset = (region.x, max(top, 0))

    def _accept(self) -> bool:
        if not self.is_open or self._list.highlighted is None:
            return False
        self.value = str(self._list.get_option_at_index(
            self._list.highlighted).prompt)
        self.cursor_position = len(self.value)
        self.close()
        return True

    def _move(self, delta: int) -> None:
        if not self.is_open:
            self.refresh_completions()
            if not self.is_open:
                return
        count = self._list.option_count
        current = self._list.highlighted
        self._list.highlighted = (0 if current is None
                                  else (current + delta) % count)

    # -- events ------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.has_focus:
            self.refresh_completions()

    def on_blur(self) -> None:
        self.close()

    async def _on_key(self, event: events.Key) -> None:
        if event.key in ('down', 'up'):
            self._move(1 if event.key == 'down' else -1)
            event.stop()
            event.prevent_default()
            return
        if event.key in ('enter', 'tab') and self._accept():
            event.stop()
            event.prevent_default()
            return
        if event.key == 'escape' and self.is_open:
            self.close()
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


class PromptScreen(ModalScreen[str | None]):
    """One line of text, or None if the user changed their mind."""

    BINDINGS = [('escape', 'dismiss_prompt', 'Cancel')]

    def __init__(self, title: str, placeholder: str = '') -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal'):
            yield Label(self.title_text)
            yield Input(placeholder=self.placeholder, id='prompt')

    def on_mount(self) -> None:
        self.query_one('#prompt', Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_dismiss_prompt(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [('escape', 'refuse', 'Cancel')]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal'):
            yield Label(self.question)
            yield Button('Delete', variant='error', id='confirm')
            yield Button('Cancel', id='cancel')

    def on_mount(self) -> None:
        self.query_one('#confirm', Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == 'confirm')

    def action_refuse(self) -> None:
        self.dismiss(False)

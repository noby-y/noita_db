"""The TUI.

Reads and writes the database stage 1 built, and opens nothing else. Every
view is a query, run directly on each keystroke — the largest component table
holds 4581 rows and SQLite answers a full filter query in about a millisecond,
so there is no cache here and should not be one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.widgets import (Button, ContentSwitcher, DataTable, Footer, Header,
                             Input, Label, OptionList, Static, Tree)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from . import catalog as catalog_module
from . import db, detail, groups, spell_detail
from .filter_row import FieldRow, FilterRow
from .query import (COMBINATORS, ORDERING_OPERATORS, Query, Scope, entity_query,
                    problem)
from .spell_query import SpellScope, spell_query
from .spell_query import problem as spell_problem
from .spell_row import SpellFilterRow
from .widgets import CompletionInput, ConfirmScreen, PromptScreen

ALL_ENTITIES = 'all entities'
ENTITIES_VIEW, SPELLS_VIEW = 'entities-view', 'spells-view'
# Tags are wide, rarely readable in a column, and already in the detail pane.
# The widths are fixed: an auto-sized column measures every cell it holds, which
# is the one slow part of redrawing 3776 rows on a keystroke, and the columns
# would otherwise jump about as the result changes.
RESULT_COLUMNS = (('name', 34), ('display name', 22), ('source file', 44))
SPELL_COLUMNS = (('name', 26), ('action id', 22), ('type', 18), ('mana', 5),
                 ('price', 6), ('uses', 4))


class NoitaTUI(App):
    CSS_PATH = 'app.tcss'
    TITLE = 'noita-db'

    # Short labels: eleven bindings and a wide terminal is not a given.
    BINDINGS = [
        ('ctrl+n', 'add_filter', '+filter'),
        ('ctrl+r', 'remove_filter', '-filter'),
        ('ctrl+t', 'cycle_combinator', 'and/or/xor'),
        ('f2', 'new_group', '+group'),
        ('f3', 'delete_group', '-group'),
        ('f4', 'add_entity', '+entity'),
        ('f5', 'add_all', '+all'),
        ('f6', 'remove_entity', '-entity'),
        ('f7', 'toggle_scope', 'scope'),
        ('f8', 'clear_scope', 'clear'),
        ('f9', 'switch_view', 'view'),
        ('ctrl+q', 'quit', 'quit'),
    ]

    def __init__(self, database: Path | str = 'noita.db') -> None:
        super().__init__()
        self.database_path = Path(database)
        self.connection = db.connect(self.database_path)
        self.catalog = catalog_module.load(self.connection)
        self.spell_catalog = catalog_module.load_spells(self.connection)
        self.combinator = 'AND'
        self.spell_combinator = 'AND'
        self.group_id: int | None = None     # the group add/remove writes to
        self.scoped = False                  # …and whether it also restricts
        self.current_query = Query('1')
        self.current_spell_query = Query('1', source='spells s')
        self._entity_ids: dict[str, int] = {}
        self._spell_ids: dict[str, int] = {}
        self._value_cache: dict[tuple[str, str], list[str]] = {}

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with ContentSwitcher(initial=ENTITIES_VIEW):
            with Vertical(id=ENTITIES_VIEW):
                with Horizontal(classes='toolbar'):
                    yield Input(placeholder='search name, display name or file',
                                id='search')
                    yield CompletionInput(lambda: list(self.catalog.tags),
                                          placeholder='tag', id='tag')
                    yield Button('match: AND', id='combinator')
                    yield Button('+ filter', id='add-filter')
                yield VerticalScroll(id='filters', classes='filters')
                with Horizontal(classes='main'):
                    with Vertical(id='groups-pane'):
                        yield Label('Groups')
                        yield OptionList(id='groups')
                    with Vertical(classes='results-pane'):
                        yield DataTable(id='results', cursor_type='row',
                                        zebra_stripes=True)
                    with Vertical(classes='detail-pane'):
                        yield Tree('nothing selected', id='detail')
            with Vertical(id=SPELLS_VIEW):
                with Horizontal(classes='toolbar'):
                    yield Input(placeholder='search spell name, id or description',
                                id='spell-search')
                    yield Button('match: AND', id='spell-combinator')
                    yield Button('+ filter', id='spell-add-filter')
                yield VerticalScroll(id='spell-filters', classes='filters')
                with Horizontal(classes='main'):
                    with Vertical(classes='results-pane'):
                        yield DataTable(id='spell-results', cursor_type='row',
                                        zebra_stripes=True)
                    with Vertical(classes='detail-pane'):
                        yield Tree('nothing selected', id='spell-detail')
        yield Static('', id='status')
        yield Footer()

    def on_mount(self) -> None:
        for table_id, columns in (('#results', RESULT_COLUMNS),
                                  ('#spell-results', SPELL_COLUMNS)):
            table = self.query_one(table_id, DataTable)
            for column, width in columns:
                table.add_column(column, width=width, key=column)
        self.refresh_groups()
        self.add_filter_row(focus=False)
        self.add_spell_filter_row(focus=False)
        self.run_spell_query()
        self.run_query()
        self.query_one('#search', Input).focus()
        info = db.build_info(self.connection)
        self.sub_title = (f'{self.database_path} · {info.get("entities", "?")} entities'
                          f' · {self.spell_catalog.spells} spells · built '
                          f'{info.get("built_at", "?")[:10]}')

    # -- views -------------------------------------------------------------

    @property
    def view(self) -> str:
        return self.query_one(ContentSwitcher).current or ENTITIES_VIEW

    @property
    def on_spells(self) -> bool:
        return self.view == SPELLS_VIEW

    def action_switch_view(self) -> None:
        spells = not self.on_spells
        self.query_one(ContentSwitcher).current = (
            SPELLS_VIEW if spells else ENTITIES_VIEW)
        # Each view owns its filters and its status line, so the one being
        # switched to re-runs rather than showing the other one's count.
        if spells:
            self.run_spell_query()
            self.query_one('#spell-search', Input).focus()
        else:
            self.run_query()
            self.query_one('#search', Input).focus()

    # -- completion sources ------------------------------------------------

    def component_types(self) -> list[str]:
        return self.catalog.type_names

    def attributes(self, component_type: str) -> list[str]:
        return self.catalog.attribute_names(component_type)

    def all_attributes(self) -> list[str]:
        return sorted(self.catalog.types_by_attribute)

    def owners(self, attribute: str) -> list[str]:
        return list(self.catalog.types_by_attribute.get(attribute, ()))

    def values(self, component_type: str, attribute: str) -> list[str]:
        if self.catalog.attribute(component_type, attribute) is None:
            return []
        key = (component_type, attribute)
        # The one thing held between keystrokes: a DISTINCT over a column is
        # the only query here that touches every row of a table, and the value
        # field re-asks on each character typed.
        if key not in self._value_cache:
            self._value_cache[key] = catalog_module.distinct_values(
                self.connection, component_type, attribute)
        return self._value_cache[key]

    # -- completion sources, spell side ------------------------------------

    def spell_sources(self) -> list[str]:
        return self.spell_catalog.sources

    def spell_keys(self, source: str) -> list[str]:
        return self.spell_catalog.keys(source)

    def spell_values(self, source: str, key: str) -> list[str]:
        cached = self._value_cache.get((source, key))
        if cached is not None:
            return cached
        if source in self.spell_catalog.effect_keys:
            # The value of a write lands in value_num or value_text, so the
            # completion has to look through both.
            values = catalog_module.effect_values(self.connection, source, key)
        elif self.spell_catalog.column(source, key) is None:
            values = []
        else:
            table = (catalog_module.LINK_TABLES.get(source)
                     or ('spells' if source == catalog_module.SPELL_SOURCE
                         else 'spell_effects'))
            values = catalog_module.distinct_values(self.connection, table, key)
        self._value_cache[(source, key)] = values
        return values

    # -- querying ----------------------------------------------------------

    @property
    def filter_rows(self) -> list[FilterRow]:
        return list(self.query(FilterRow))

    def current_scope(self) -> Scope:
        return Scope(group_id=self.group_id if self.scoped else None,
                     tag=self.query_one('#tag', Input).value.strip() or None,
                     text=self.query_one('#search', Input).value.strip())

    def run_query(self) -> None:
        active, broken = [], []
        for row in self.filter_rows:
            row.remove_class('invalid')
            if not row.ready or row.blank:
                continue
            item = row.filter
            failure = problem(item, self.catalog)
            if failure is None:
                active.append(item)
            else:
                row.add_class('invalid')
                broken.append(failure)

        self.current_query = entity_query(
            active, self.combinator, self.current_scope(), self.catalog)
        sql, params = self.current_query.select()
        try:
            rows = self.connection.execute(sql, params).fetchall()
        except sqlite3.Error as error:            # a malformed query is a bug,
            self.set_status(f'query failed: {error}', error=True)   # but say so
            return
        self.fill_results(rows)
        self.report(len(rows), active, broken)

    def fill_results(self, rows: list[sqlite3.Row]) -> None:
        table = self.query_one('#results', DataTable)
        table.clear()
        self._entity_ids = {}
        for row in rows:
            self._entity_ids[row['entity_key']] = row['id']
            table.add_row(row['name'], row['display_name'] or '',
                          row['source_file'], key=row['entity_key'])
        if rows:
            table.move_cursor(row=0)
            self.show_detail(rows[0]['id'])
        else:
            self.clear_detail('no matches')

    def report(self, matched: int, active: list, broken: list[str]) -> None:
        count = len(active)
        parts = [f'{matched} entities', f'{count} filter{"" if count == 1 else "s"}',
                 self.combinator]
        if self.group_id is not None:
            parts.append(('in ' if self.scoped else 'target ')
                         + self.group_name(self.group_id))
        if broken:
            parts.append('· ' + '; '.join(sorted(set(broken))))
        self.set_status(' · '.join(parts), error=bool(broken))
        self.set_column_hint(active)

    def set_column_hint(self, active: list) -> None:
        """Warn when an ordering comparison is aimed at a column holding text.

        Roughly 2180 attributes are INTEGER at runtime but name a material in
        the XML, and SQLite sorts every number before every text value, so
        `material > 5` quietly skips them all.
        """
        for item in active:
            if item.op not in ORDERING_OPERATORS or not item.attribute:
                continue
            kinds = catalog_module.storage_types(
                self.connection, item.component_type, item.attribute)
            text_rows = kinds.get('text', 0)
            if text_rows:
                total = sum(kinds.values())
                self.set_status(
                    f'{item.component_type}.{item.attribute} holds text in '
                    f'{text_rows} of {total} rows — {item.op} skips those',
                    error=True)
                return

    # -- querying, spell side ----------------------------------------------

    @property
    def spell_filter_rows(self) -> list[SpellFilterRow]:
        return list(self.query(SpellFilterRow))

    def run_spell_query(self) -> None:
        active, broken = [], []
        for row in self.spell_filter_rows:
            row.remove_class('invalid')
            if not row.ready or row.blank:
                continue
            item = row.filter
            failure = spell_problem(item, self.spell_catalog)
            if failure is None:
                active.append(item)
            else:
                row.add_class('invalid')
                broken.append(failure)

        text = self.query_one('#spell-search', Input).value.strip()
        self.current_spell_query = spell_query(
            active, self.spell_combinator, SpellScope(text), self.spell_catalog)
        sql, params = self.current_spell_query.select(
            columns='s.id, s.action_id, s.display_name, s.type, s.mana, s.price,'
                    ' s.max_uses',
            order='COALESCE(s.display_name, s.action_id)')
        try:
            rows = self.connection.execute(sql, params).fetchall()
        except sqlite3.Error as error:
            self.set_status(f'query failed: {error}', error=True)
            return

        table = self.query_one('#spell-results', DataTable)
        table.clear()
        self._spell_ids = {}
        for row in rows:
            self._spell_ids[row['action_id']] = row['id']
            table.add_row(row['display_name'] or '', row['action_id'],
                          _spell_type(row['type']), _number(row['mana']),
                          _number(row['price']), _number(row['max_uses']),
                          key=row['action_id'])
        if rows:
            table.move_cursor(row=0)
            self.show_spell(rows[0]['id'])
        else:
            self.query_one('#spell-detail', Tree).reset('no matches')

        count = len(active)
        parts = [f'{len(rows)} spells', f'{count} filter{"" if count == 1 else "s"}',
                 self.spell_combinator]
        if broken:
            parts.append('· ' + '; '.join(sorted(set(broken))))
        self.set_status(' · '.join(parts), error=bool(broken))

    def show_spell(self, spell_id: int) -> None:
        tree = self.query_one('#spell-detail', Tree)
        row = spell_detail.spell(self.connection, spell_id)
        if row is None:
            tree.reset('gone')
            return
        tree.reset(row['display_name'] or row['action_id'])
        tree.root.expand()

        about = tree.root.add('properties', expand=True)
        for key, value in spell_detail.properties(self.connection, spell_id):
            about.add_leaf(f'{key}: {value}')

        effects = spell_detail.effects(self.connection, spell_id)
        branch = tree.root.add(f'effects ({len(effects)})', expand=True)
        for effect in effects:
            branch.add_leaf(str(effect))

        grouped: dict[str, list[str]] = {}
        for heading, line in spell_detail.links(self.connection, spell_id):
            grouped.setdefault(heading, []).append(line)
        for heading, lines in grouped.items():
            branch = tree.root.add(f'{heading} ({len(lines)})')
            for line in lines:
                branch.add_leaf(line)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        if event.data_table.id == 'spell-results':
            spell_id = self._spell_ids.get(key)
            if spell_id is not None:
                self.show_spell(spell_id)
            return
        entity_id = self._entity_ids.get(key)
        if entity_id is not None:
            self.show_detail(entity_id)

    def set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one('#status', Static)
        status.set_class(error, 'error')
        status.update(message)

    # -- detail pane -------------------------------------------------------

    def clear_detail(self, label: str) -> None:
        tree = self.query_one('#detail', Tree)
        tree.reset(label)

    def show_detail(self, entity_id: int) -> None:
        row = detail.entity(self.connection, entity_id)
        if row is None:
            self.clear_detail('gone')
            return
        tree = self.query_one('#detail', Tree)
        tree.reset(row['display_name'] or row['name'])
        tree.root.expand()

        about = tree.root.add('entity', expand=True)
        for field in ('entity_key', 'name', 'display_name', 'source_file',
                      'defined_in', 'entity_path', 'tags', 'serialize'):
            about.add_leaf(f'{field}: {_text(row[field])}')
        for base in detail.bases(self.connection, entity_id):
            about.add_leaf(f'base: {base["base_file"]}'
                           + ('' if base['found'] else '  (missing)'))
        for child in detail.children(self.connection, entity_id):
            about.add_leaf(f'child: {child["name"]} ({child["entity_path"]})')
        member_of = groups.membership(self.connection, row['entity_key'])
        about.add_leaf(f'groups: {", ".join(member_of) if member_of else "none"}')

        unimplemented, duplicates = detail.counts(self.connection, entity_id)
        components = detail.components(self.connection, entity_id)
        summary = f'components: {len(components)}'
        if unimplemented or duplicates:
            summary += f'  ({unimplemented} unimplemented, {duplicates} duplicate)'
        about.add_leaf(summary)

        for component in components:
            label = component.label
            if not component.documented:
                label += '  (undocumented)'
            if component.enabled == 0:
                label += '  (disabled)'
            # Loaded when expanded: ProjectileComponent alone is 251 columns.
            tree.root.add(label, data=component)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node: TreeNode = event.node
        component = node.data
        if not isinstance(component, detail.ComponentRow) or node.children:
            return
        for name, value in detail.values(self.connection, component, self.catalog):
            node.add_leaf(f'{name} = {value}')
        for path, value in detail.unimplemented(self.connection, component.id):
            node.add_leaf(f'[unimplemented] {path} = {value}')
        for key, value in detail.duplicates(self.connection, component.id):
            node.add_leaf(f'[duplicate] {key} = {value}')
        if not node.children:
            node.add_leaf('(no attributes)')

    # -- groups ------------------------------------------------------------

    def refresh_groups(self) -> None:
        option_list = self.query_one('#groups', OptionList)
        option_list.clear_options()
        option_list.add_option(Option(ALL_ENTITIES, id='all'))
        for group in groups.list_groups(self.connection):
            option_list.add_option(
                Option(f'{group.name} ({group.members})', id=str(group.id)))

    def group_name(self, group_id: int) -> str:
        return next((group.name for group in groups.list_groups(self.connection)
                     if group.id == group_id), str(group_id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != 'groups':
            return
        option_id = event.option.id
        self.group_id = None if option_id == 'all' else int(option_id)
        # Picking a group out of the list means wanting to look at it.
        self.scoped = self.group_id is not None
        self.run_query()

    @property
    def highlighted_key(self) -> str | None:
        table = self.query_one('#results', DataTable)
        if table.row_count == 0 or table.cursor_row < 0:
            return None
        cell = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
        return str(cell.row_key.value)

    def entities_only(self) -> bool:
        """Groups hold entity keys. There is no such thing as a spell group."""
        if self.on_spells:
            self.notify('groups apply to entities, not spells', severity='warning')
            return False
        return True

    def require_group(self) -> int | None:
        if not self.entities_only():
            return None
        if self.group_id is None:
            self.notify('pick a group in the left pane first', severity='warning')
            return None
        return self.group_id

    def action_new_group(self) -> None:
        if not self.entities_only():
            return

        def created(name: str | None) -> None:
            if not name:
                return
            try:
                group = groups.create(self.connection, name)
            except groups.DuplicateGroup as error:
                self.notify(str(error), severity='error')
                return
            self.group_id = group.id
            # A group is empty the moment it is made, so scoping to it now
            # would hide the very result the user meant to put in it.
            self.scoped = False
            self.refresh_groups()
            self.run_query()
        self.push_screen(PromptScreen('Name the new group', 'group name'), created)

    def action_delete_group(self) -> None:
        group_id = self.require_group()
        if group_id is None:
            return
        name = self.group_name(group_id)

        def confirmed(yes: bool) -> None:
            if not yes:
                return
            groups.delete(self.connection, group_id)
            self.group_id = None
            self.scoped = False
            self.refresh_groups()
            self.run_query()
            self.notify(f'deleted {name} and its membership')
        self.push_screen(ConfirmScreen(f'Delete {name} and its members?'), confirmed)

    def action_add_entity(self) -> None:
        group_id, key = self.require_group(), self.highlighted_key
        if group_id is None or key is None:
            return
        added = groups.add_entity(self.connection, group_id, key)
        self.refresh_groups()
        self.notify(f'{key} {"added to" if added else "already in"} '
                    f'{self.group_name(group_id)}')
        if self.scoped:
            self.run_query()

    def action_remove_entity(self) -> None:
        group_id, key = self.require_group(), self.highlighted_key
        if group_id is None or key is None:
            return
        removed = groups.remove_entity(self.connection, group_id, key)
        self.refresh_groups()
        self.notify(f'{key} {"removed from" if removed else "was not in"} '
                    f'{self.group_name(group_id)}')
        self.run_query()

    def action_add_all(self) -> None:
        group_id = self.require_group()
        if group_id is None:
            return
        # One statement, however many rows matched: the filter goes in as a
        # subquery rather than the ids coming back through Python.
        added = groups.add_result(self.connection, group_id, self.current_query)
        self.refresh_groups()
        self.notify(f'added {added} entities to {self.group_name(group_id)}')
        self.run_query()

    def action_toggle_scope(self) -> None:
        if self.require_group() is None:
            return
        self.scoped = not self.scoped
        self.run_query()

    def action_clear_scope(self) -> None:
        if self.on_spells:
            self.query_one('#spell-search', Input).value = ''
            self.run_spell_query()
            return
        self.group_id = None
        self.scoped = False
        self.query_one('#tag', Input).value = ''
        self.query_one('#search', Input).value = ''
        self.run_query()

    # -- filters -----------------------------------------------------------

    def action_add_filter(self) -> None:
        if self.on_spells:
            self.add_spell_filter_row(focus=True)
        else:
            self.add_filter_row(focus=True)

    def add_filter_row(self, focus: bool) -> None:
        # New filters join binding #1: wanting one component to satisfy two
        # conditions is the ordinary case.
        self._mount_row(FilterRow(self), '#filters', 'type', focus)

    def add_spell_filter_row(self, focus: bool) -> None:
        self._mount_row(SpellFilterRow(self), '#spell-filters', 'source', focus)

    def _mount_row(self, row: FieldRow, container: str, first: str,
                   focus: bool) -> None:
        self.query_one(container, VerticalScroll).mount(row)
        # The row composes after this returns, so the focus has to wait for it.
        # At startup it does not happen at all: the search field is the better
        # place to land.
        if focus:
            self.call_after_refresh(lambda: row.query_one(f'#{first}', Input).focus())

    def action_remove_filter(self) -> None:
        rows = self.spell_filter_rows if self.on_spells else self.filter_rows
        if not rows:
            return
        focused = next((row for row in rows if row.has_focus_within), None)
        (focused or rows[-1]).remove()
        self.call_after_refresh(self.rerun)

    def action_cycle_combinator(self) -> None:
        if self.on_spells:
            self.spell_combinator = _next_combinator(self.spell_combinator)
            self.query_one('#spell-combinator', Button).label = \
                f'match: {self.spell_combinator}'
        else:
            self.combinator = _next_combinator(self.combinator)
            self.query_one('#combinator', Button).label = f'match: {self.combinator}'
        self.rerun()

    def rerun(self) -> None:
        """Re-run whichever view is on screen."""
        if self.on_spells:
            self.run_spell_query()
        else:
            self.run_query()

    def on_field_row_edited(self, event: FieldRow.Edited) -> None:
        # A row in the view that is off screen cannot have been edited by the
        # user: that is a field posting its initial value as it mounts, and
        # acting on it would leave the other view's count in the status line.
        if isinstance(event.row, SpellFilterRow) == self.on_spells:
            self.rerun()

    def on_field_row_removed(self, event: FieldRow.Removed) -> None:
        spells = isinstance(event.row, SpellFilterRow)
        event.row.remove()
        self.call_after_refresh(
            self.run_spell_query if spells else self.run_query)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ('search', 'tag'):
            self.run_query()
        elif event.input.id == 'spell-search':
            self.run_spell_query()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ('add-filter', 'spell-add-filter'):
            self.action_add_filter()
        elif event.button.id in ('combinator', 'spell-combinator'):
            self.action_cycle_combinator()


def _text(value: object) -> str:
    return '' if value is None else str(value)


def _number(value: object) -> str:
    """NULL in a spell column means the key is absent from the Lua table, which
    reads better as an empty cell than as the word None."""
    return '' if value is None else str(value)


def _spell_type(value: str | None) -> str:
    return (value or '').removeprefix('ACTION_TYPE_').lower()


def _next_combinator(current: str) -> str:
    return COMBINATORS[(COMBINATORS.index(current) + 1) % len(COMBINATORS)]

"""The app itself, driven through Textual's pilot.

`run_test` is async and the rest of the suite is not, so each scenario is a
coroutine handed to `drive` rather than a dependency on an async plugin.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import DataTable, Input, OptionList, Static, Tree

from noita_tui.app import NoitaTUI
from noita_tui.filter_row import FilterRow
from noita_tui.spell_row import SpellFilterRow
from noita_tui.widgets import CompletionInput


def drive(app: NoitaTUI, scenario) -> None:
    async def run() -> None:
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await scenario(pilot)
    asyncio.run(run())


@pytest.fixture
def app(built_database):
    return NoitaTUI(built_database)


def results(app: NoitaTUI) -> list[str]:
    table = app.query_one('#results', DataTable)
    return [table.get_row_at(index)[0] for index in range(table.row_count)]


def status(app: NoitaTUI) -> str:
    return str(app.query_one('#status', Static).render())


def first_row(app: NoitaTUI) -> FilterRow:
    return app.query_one(FilterRow)


def test_startup_lists_every_entity(app):
    async def scenario(pilot):
        assert len(results(app)) == 9
        assert '9 entities' in status(app)
    drive(app, scenario)


def test_focus_starts_in_the_search_and_follows_a_new_filter(app):
    async def scenario(pilot):
        assert app.query_one('#search', Input).has_focus
        await pilot.press('ctrl+n')
        await pilot.pause()
        assert len(app.filter_rows) == 2
        assert app.filter_rows[-1].query_one('#type', Input).has_focus
    drive(app, scenario)


def test_search_narrows_on_every_keystroke(app):
    async def scenario(pilot):
        app.query_one('#search', Input).focus()
        await pilot.press(*'fuse')
        await pilot.pause()
        # base_enemy is the nested entity inside fuse.xml: the search covers
        # the source file, not just the name.
        assert results(app) == ['base_enemy', 'fuse']
    drive(app, scenario)


def test_a_filter_row_runs_as_soon_as_it_is_complete(app):
    async def scenario(pilot):
        row = first_row(app)
        row.query_one('#type', Input).value = 'ProjectileComponent'
        await pilot.pause()
        assert results(app) == ['base_projectile', 'projectile_default']

        row.query_one('#attribute', Input).value = 'damage'
        row.query_one('#op', Input).value = '='
        row.query_one('#value', Input).value = '0.12'
        await pilot.pause()
        assert results(app) == ['projectile_default']
    drive(app, scenario)


def test_a_half_typed_row_is_marked_rather_than_run(app):
    async def scenario(pilot):
        row = first_row(app)
        row.query_one('#type', Input).value = 'Projectile'
        await pilot.pause()
        assert row.has_class('invalid')
        assert 'unknown component' in status(app)
        assert len(results(app)) == 9      # the query ran without the bad row
    drive(app, scenario)


def test_an_attribute_names_its_own_component(app):
    async def scenario(pilot):
        row = first_row(app)
        row.query_one('#attribute', Input).value = 'image_file'
        await pilot.pause()
        assert row.query_one('#type', Input).value == 'SpriteComponent'
    drive(app, scenario)


def test_completion_offers_what_the_database_holds(app):
    async def scenario(pilot):
        field = first_row(app).query_one('#type', CompletionInput)
        field.focus()
        await pilot.press(*'ptcomp')        # not a prefix of anything
        await pilot.pause()
        assert not field.is_open
        field.value = ''
        await pilot.press(*'Sprite')
        await pilot.pause()
        assert field.is_open
        assert field.matches() == ['SpriteComponent']
        await pilot.press('down', 'tab')
        await pilot.pause()
        assert field.value == 'SpriteComponent'
        assert not field.is_open
    drive(app, scenario)


def test_the_combinator_applies_to_the_whole_set(app):
    async def scenario(pilot):
        first_row(app).query_one('#type', Input).value = 'MaterialInventoryComponent'
        app.action_add_filter()
        await pilot.pause()
        app.query(FilterRow).last().query_one('#type', Input).value = 'SpriteComponent'
        await pilot.pause()
        assert results(app) == []
        await pilot.press('ctrl+t')
        await pilot.pause()
        assert app.combinator == 'OR'
        assert results(app) == ['base_enemy', 'fuse', 'projectile_default']
    drive(app, scenario)


def test_detail_pane_follows_the_highlighted_row(app):
    async def scenario(pilot):
        app.query_one('#search', Input).focus()
        await pilot.press(*'light_bullet')
        await pilot.pause()
        tree = app.query_one('#detail', Tree)
        assert str(tree.root.label) == 'Default projectile'
        labels = [str(node.label) for node in tree.root.children]
        assert 'ProjectileComponent #2' in labels

        component = next(node for node in tree.root.children
                         if str(node.label) == 'ProjectileComponent #2')
        component.expand()
        await pilot.pause()
        assert 'damage = 0.12' in [str(leaf.label) for leaf in component.children]
    drive(app, scenario)


def test_groups_are_created_scoped_and_filled(app):
    async def scenario(pilot):
        await pilot.press('f2')
        await pilot.pause()
        await pilot.press(*'lua')
        await pilot.press('enter')
        await pilot.pause()
        assert app.group_id is not None

        # A new group does not scope the view, or the result about to go into
        # it would be hidden by the group being empty.
        assert not app.scoped
        first_row(app).query_one('#type', Input).value = 'LuaComponent'
        await pilot.pause()
        await pilot.press('f5')             # add every match
        await pilot.pause()
        options = app.query_one('#groups', OptionList)
        assert str(options.get_option_at_index(1).prompt) == 'lua (3)'

        # Scoped to the group, an empty filter set shows exactly its members.
        first_row(app).query_one('#type', Input).value = ''
        await pilot.press('f7')
        await pilot.pause()
        assert results(app) == ['base_projectile', 'homing', 'projectile_default']
    drive(app, scenario)


def spells(app: NoitaTUI) -> list[str]:
    table = app.query_one('#spell-results', DataTable)
    return [table.get_row_at(index)[1] for index in range(table.row_count)]


def test_f9_switches_to_the_spells_view_and_back(app):
    async def scenario(pilot):
        await pilot.press('f9')
        await pilot.pause()
        assert app.on_spells
        assert spells(app) == ['HOMING', 'LIGHT_BULLET', 'TRIGGER']
        assert '3 spells' in status(app)
        assert app.query_one('#spell-search', Input).has_focus

        await pilot.press('f9')
        await pilot.pause()
        assert not app.on_spells
        assert '9 entities' in status(app)
    drive(app, scenario)


def test_a_spell_filter_narrows_the_list(app):
    async def scenario(pilot):
        await pilot.press('f9')
        await pilot.pause()
        row = app.query_one(SpellFilterRow)
        row.query_one('#source', Input).value = 'c'
        row.query_one('#key', Input).value = 'screenshake'
        await pilot.pause()
        assert spells(app) == ['LIGHT_BULLET']

        # The combinator and the filter keys belong to the view on screen.
        await pilot.press('ctrl+t')
        await pilot.pause()
        assert app.spell_combinator == 'OR'
        assert app.combinator == 'AND'
    drive(app, scenario)


def test_a_half_typed_spell_row_is_marked_rather_than_run(app):
    async def scenario(pilot):
        await pilot.press('f9')
        await pilot.pause()
        row = app.query_one(SpellFilterRow)
        row.query_one('#source', Input).value = 'spell'
        await pilot.pause()
        assert row.has_class('invalid')
        assert 'no column' in status(app)
        assert len(spells(app)) == 3
    drive(app, scenario)


def test_the_spell_detail_shows_effects_and_links(app):
    async def scenario(pilot):
        await pilot.press('f9')
        await pilot.pause()
        app.query_one('#spell-search', Input).focus()
        await pilot.press(*'homing')
        await pilot.pause()
        tree = app.query_one('#spell-detail', Tree)
        assert str(tree.root.label) == 'Homing'
        branches = [str(node.label) for node in tree.root.children]
        assert 'effects (6)' in branches
        assert 'extra entities (2)' in branches
        effects = next(node for node in tree.root.children
                       if str(node.label).startswith('effects'))
        assert 'c.speed_multiplier mul 0.75' in [str(n.label) for n in effects.children]
    drive(app, scenario)


def test_f10_exports_the_filtered_result(app, tmp_path):
    target = tmp_path / 'lua.txt'

    async def scenario(pilot):
        first_row(app).query_one('#type', Input).value = 'LuaComponent'
        await pilot.pause()
        await pilot.press('f10')
        await pilot.pause()
        prompt = app.screen.query_one('#prompt', Input)
        assert prompt.value == 'entities.txt'   # a default, offered pre-selected
        prompt.value = str(target)
        await pilot.press('enter')
        await pilot.pause()
        assert target.read_text(encoding='utf-8').splitlines() == [
            'data/entities/base_projectile.xml',
            'data/entities/misc/homing.xml',
            'data/entities/projectiles/light_bullet.xml',
        ]
    drive(app, scenario)


def test_an_export_over_an_existing_file_asks_first(app, tmp_path):
    target = tmp_path / 'taken.txt'
    target.write_text('do not lose me\n', encoding='utf-8')

    async def scenario(pilot):
        await pilot.press('f10')
        await pilot.pause()
        app.screen.query_one('#prompt', Input).value = str(target)
        await pilot.press('enter')
        await pilot.pause()
        await pilot.press('escape')         # refuse the overwrite
        await pilot.pause()
        assert target.read_text(encoding='utf-8') == 'do not lose me\n'

        await pilot.press('f10')
        await pilot.pause()
        app.screen.query_one('#prompt', Input).value = str(target)
        await pilot.press('enter')
        await pilot.pause()
        await pilot.press('enter')         # the Overwrite button has the focus
        await pilot.pause()
        assert len(target.read_text(encoding='utf-8').splitlines()) == 9
    drive(app, scenario)


def test_export_says_no_on_the_spells_view(app, tmp_path):
    async def scenario(pilot):
        await pilot.press('f9')
        await pilot.pause()
        await pilot.press('f10')
        await pilot.pause()
        assert app.screen is app.screen_stack[0]     # no prompt opened
    drive(app, scenario)


def test_group_keys_say_no_on_the_spells_view(app):
    async def scenario(pilot):
        await pilot.press('f9')
        await pilot.pause()
        await pilot.press('f2')             # would open the new-group prompt
        await pilot.pause()
        assert app.screen is app.screen_stack[0]
        assert app.group_id is None
    drive(app, scenario)


def test_a_single_entity_goes_in_and_out_of_a_group(app):
    async def scenario(pilot):
        await pilot.press('f2')
        await pilot.pause()
        await pilot.press(*'keep')
        await pilot.press('enter')
        await pilot.pause()

        table = app.query_one('#results', DataTable)
        table.focus()
        table.move_cursor(row=results(app).index('fuse'))
        await pilot.pause()
        await pilot.press('f4')
        await pilot.press('f7')             # scope to the group
        await pilot.pause()
        assert results(app) == ['fuse']

        await pilot.press('f6')
        await pilot.pause()
        assert results(app) == []
        await pilot.press('f8')             # clear the scope
        await pilot.pause()
        assert len(results(app)) == 9
    drive(app, scenario)

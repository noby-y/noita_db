from pathlib import Path

import pytest

from noita_db import spells


@pytest.fixture
def by_id(data_root: Path):
    parsed = spells.load(data_root / 'data/scripts/gun/gun_actions.lua')
    return {spell.action_id: spell for spell in parsed}


def effects(spell, scope, key):
    return [e for e in spell.effects if e.scope == scope and e.key == key]


def test_commented_out_entries_are_not_spells(by_id):
    assert set(by_id) == {'LIGHT_BULLET', 'HOMING', 'TRIGGER'}


def test_literal_properties_are_read(by_id):
    light = by_id['LIGHT_BULLET'].properties
    assert light['name'] == '$action_light_bullet'
    assert light['price'] == 100
    assert light['mana'] == 5
    assert light['type'] == 'ACTION_TYPE_PROJECTILE'
    assert by_id['HOMING'].properties['never_unlimited'] is True


def test_related_lists_go_to_their_own_links(by_id):
    assert by_id['LIGHT_BULLET'].related_projectiles == \
        [('data/entities/projectiles/light_bullet.xml', 1)]
    assert by_id['HOMING'].related_extra_entities == ['data/entities/misc/homing.xml']


def test_addition_is_stored_as_key_op_value(by_id):
    (effect,) = effects(by_id['LIGHT_BULLET'], 'c', 'screenshake')
    assert (effect.op, effect.value_num) == ('add', 10.0)


def test_subtraction_multiplication_and_min(by_id):
    # Stored as an `add` of a negative number, so `value_num < 0` finds it.
    trigger = effects(by_id['TRIGGER'], 'c', 'fire_rate_wait')[0]
    assert (trigger.op, trigger.value_num) == ('add', -2.0)
    multiply, clamp = effects(by_id['HOMING'], 'c', 'speed_multiplier')
    assert (multiply.op, multiply.value_num) == ('mul', 0.75)
    assert (clamp.op, clamp.value_num, clamp.conditional) == ('min', 20.0, True)


def test_string_assignment_keeps_the_text(by_id):
    (effect,) = effects(by_id['HOMING'], 'c', 'material')
    assert (effect.op, effect.value_text) == ('set', 'fire')


def test_loose_globals_are_captured(by_id):
    (effect,) = effects(by_id['LIGHT_BULLET'], 'global', 'current_reload_time')
    assert (effect.op, effect.value_num) == ('add', 4.0)


def test_shot_effects_are_captured(by_id):
    (effect,) = effects(by_id['HOMING'], 'shot_effects', 'recoil_knockback')
    assert (effect.op, effect.value_num) == ('add', 5.0)


def test_a_variable_is_not_mistaken_for_a_string(by_id):
    (effect,) = effects(by_id['HOMING'], 'c', 'fire_rate_wait')
    assert effect.op == 'expr'
    assert effect.value_text == 'firerate'


def test_extra_entities_are_split_on_commas(by_id):
    assert by_id['HOMING'].extra_entities == [
        'data/entities/misc/homing.xml',
        'data/entities/particles/spark.xml',
    ]


def test_add_projectile_calls_are_linked(by_id):
    (link,) = by_id['LIGHT_BULLET'].projectiles
    assert link.kind == 'add_projectile'
    assert link.entity_file == 'data/entities/projectiles/light_bullet.xml'


def test_trigger_calls_keep_their_arguments(by_id):
    timer, indirect = by_id['TRIGGER'].projectiles
    assert timer.kind == 'add_projectile_trigger_timer'
    assert timer.entity_file == 'data/entities/projectiles/fuse.xml'
    assert (timer.delay_frames, timer.draw_count) == (20, 1)
    # Called with a variable: recorded, but not resolvable to a file.
    assert indirect.entity_file is None
    assert indirect.expression == 'target'

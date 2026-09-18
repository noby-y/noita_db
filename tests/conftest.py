"""A miniature data root, so the build can be tested without the game files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COMP_DOCS = {
    'VelocityComponent': [
        ('float', 'gravity_y', '0'),
        ('float', 'air_friction', '0'),
        ('float', 'mass', '1'),
        ('vec2', 'mVelocity', '(0, 0)'),
    ],
    'ProjectileComponent': [
        ('int', 'lifetime', '-1'),
        ('float', 'damage', '1'),
        ('bool', 'on_collision_die', 'true'),
        ('ConfigExplosion', 'config_explosion', ''),
        ('PROJECTILE_TYPE::Enum', 'projectile_type', 'PROJECTILE'),
    ],
    'LuaComponent': [
        ('std::string', 'script_source_file', ''),
        ('int', 'execute_every_n_frame', '-1'),
    ],
    'SpriteComponent': [
        ('std::string', 'image_file', ''),
        ('float', 'alpha', '1'),
        ('uint32', 'color', '4294967295'),
    ],
    'PhysicsImageShapeComponent': [
        ('int', 'material', '0'),
    ],
    'MaterialInventoryComponent': [
        ('MATERIAL_VEC_DOUBLES', 'count_per_material_type', ''),
    ],
    'AreaDamageComponent': [
        ('vec2', 'aabb_min', '(-4, -4)'),
        ('types::aabb', 'box', '(0, 0) (0, 0)'),
        ('ValueRange', 'range', '[0, 1]'),
        ('types::fcolor', 'tint', 'RGBA: 1, 1, 1, 1'),
    ],
}

CONFIG_EXPLOSION = [
    ('float', 'damage', '5'),
    ('float', 'explosion_radius', '20'),
    ('ValueRange', 'physics_explosion_power', '[0, 0]'),
    ('ConfigDamageCritical', 'damage_critical', ''),
]

CONFIG_DAMAGE_CRITICAL = [
    ('int', 'chance', '0'),
    ('float', 'damage_multiplier', '2'),
]

GUN_ACTIONS = '''\
dofile_once("data/scripts/gun/gun_action_utils.lua")

actions =
{
\t{
\t\tid          = "LIGHT_BULLET",
\t\tname        = "$action_light_bullet",
\t\tdescription = "$actiondesc_light_bullet",
\t\tsprite      = "data/ui_gfx/gun_actions/light_bullet.png",
\t\ttype        = ACTION_TYPE_PROJECTILE,
\t\tspawn_level = "0,1,2",
\t\tspawn_probability = "1,1,1",
\t\tprice       = 100,
\t\tmana        = 5,
\t\trelated_projectiles = {"data/entities/projectiles/light_bullet.xml"},
\t\taction \t\t= function()
\t\t\tadd_projectile("data/entities/projectiles/light_bullet.xml")
\t\t\tc.screenshake = c.screenshake + 10.0
\t\t\tcurrent_reload_time = current_reload_time + 4
\t\tend,
\t},
\t{
\t\tid          = "HOMING",
\t\tname        = "$action_homing",
\t\tdescription = "$actiondesc_homing",
\t\tsprite      = "data/ui_gfx/gun_actions/homing.png",
\t\ttype        = ACTION_TYPE_MODIFIER,
\t\tspawn_level = "1",
\t\tspawn_probability = "1",
\t\tprice       = 180,
\t\tmana        = 20,
\t\tmax_uses    = 12,
\t\tnever_unlimited = true,
\t\trelated_extra_entities = { "data/entities/misc/homing.xml" },
\t\taction \t\t= function()
\t\t\tc.extra_entities = c.extra_entities .. "data/entities/misc/homing.xml,data/entities/particles/spark.xml,"
\t\t\tc.speed_multiplier = c.speed_multiplier * 0.75
\t\t\tc.material = "fire"
\t\t\tshot_effects.recoil_knockback = shot_effects.recoil_knockback + 5
\t\t\tif ( c.speed_multiplier >= 20 ) then
\t\t\t\tc.speed_multiplier = math.min( c.speed_multiplier, 20 )
\t\t\tend
\t\t\tlocal firerate = c.fire_rate_wait
\t\t\tc.fire_rate_wait = firerate
\t\tend,
\t},
\t--[[
\t{
\t\tid          = "NEVER_REAL",
\t\tname        = "$action_never", -- "half a quote
\t\tdescription = "$actiondesc_never",
\t\tsprite      = "data/ui_gfx/gun_actions/never.png",
\t\ttype        = ACTION_TYPE_MODIFIER,
\t\tspawn_level = "1",
\t\tspawn_probability = "1",
\t\tprice       = 10,
\t\tmana        = 1,
\t\taction \t\t= function()
\t\t\tc.fire_rate_wait = c.fire_rate_wait + 1000
\t\tend,
\t},]]--
\t{
\t\tid          = "TRIGGER",
\t\tname        = "$action_trigger",
\t\tdescription = "$actiondesc_trigger",
\t\tsprite      = "data/ui_gfx/gun_actions/trigger.png",
\t\ttype        = ACTION_TYPE_MODIFIER,
\t\tspawn_level = "2",
\t\tspawn_probability = "1",
\t\tprice       = 150,
\t\tmana        = 1,
\t\taction \t\t= function()
\t\t\tlocal target = "data/entities/projectiles/trigger.xml"
\t\t\tadd_projectile_trigger_timer("data/entities/projectiles/fuse.xml", 20, 1)
\t\t\tadd_projectile(target)
\t\t\tc.fire_rate_wait = c.fire_rate_wait - 2
\t\tend,
\t},
}
'''

COMMON_CSV = '''\
current_language,English,русский
action_light_bullet,Spark bolt,
actiondesc_light_bullet,"A weak, sparkling projectile",
action_homing,Homing,
actiondesc_homing,Homing,
action_trigger,Trigger,
actiondesc_trigger,Trigger,
projectile_default,Default projectile,
base_enemy,Base enemy,
'''

FILES = {
    # A base with two LuaComponents, so positional override matching is exercised.
    'data/entities/base_projectile.xml': '''\
<Entity tags="projectile,teleportable_NOT">
  <VelocityComponent gravity_y="400" air_friction="0.5" mass="1" />
  <ProjectileComponent lifetime="60" damage="1">
    <config_explosion damage="2" explosion_radius="10" physics_explosion_power.min="0.1" />
  </ProjectileComponent>
  <LuaComponent script_source_file="data/scripts/a.lua" />
  <LuaComponent script_source_file="data/scripts/b.lua" />
</Entity>
''',
    'data/entities/projectiles/light_bullet.xml': '''\
<Entity name="$projectile_default" tags="projectile_player">
  <Base file="data/entities/base_projectile.xml">
    <VelocityComponent gravity_y="200" air_friction="1.7" />
    <LuaComponent _remove_from_base="1" />
    <LuaComponent script_source_file="data/scripts/replaced.lua" />
    <SpriteComponent alpha="0.5" />
  </Base>
  <ProjectileComponent damage="0.12"
    ><config_explosion
      damage="0.0" <!-- a comment between attributes, with -- dashes -->
      explosion_radius="2"
      damage_critical.chance="5" ></config_explosion>
  </ProjectileComponent>
  <SpriteComponent image_file="data/spark.xml" color="ff20f050" />
  <PhysicsImageShapeComponent material="wood_prop" />
</Entity>
''',
    # Duplicate attributes, a missing base, a nested entity and a container field.
    'data/entities/projectiles/fuse.xml': '''\
<Entity>
  <Base file="data/entities/does_not_exist.xml">
    <VelocityComponent gravity_y="1" />
  </Base>
  <VelocityComponent mass="2" mass="3" mass="4" />
  <MaterialInventoryComponent>
    <count_per_material_type>
      <Material material="oil" count="200" />
      <Material material="water" count="10" />
    </count_per_material_type>
  </MaterialInventoryComponent>
  <Entity name="$base_enemy" tags="child">
    <SpriteComponent image_file="data/child.xml" />
  </Entity>
</Entity>
''',
    # Two `tags` attributes: only the first counts. Never closed, like eradicate.xml.
    'data/entities/projectiles/trigger.xml': '''\
<Entity tags="first,shared" tags="second">
  <AreaDamageComponent aabb_min.x="-8" box.min_x="-1" box.max_y="3"
                       range.max="9" tint.r="0.25" />
<Entity>
''',
    'data/entities/misc/homing.xml': '<Entity><LuaComponent /></Entity>\n',
    'data/entities/particles/spark.xml': '<Entity />\n',
    'data/entities/projectiles/trigger_unused.xml': '<Entity />\n',
    # Not an entity: must be ignored even though it lives under data/.
    'data/props_gfx/spark.xml': '<Sprite filename="spark.png" />\n',
}


def _write_docs(root: Path) -> None:
    directory = root / 'comp_docs'
    directory.mkdir(parents=True)
    everything = dict(COMP_DOCS)
    for name, fields in list(everything.items()):
        _dump(directory, name, 'Component', fields)
    _dump(directory, 'ConfigExplosion', 'ceng::IConfigBase', CONFIG_EXPLOSION)
    _dump(directory, 'ConfigDamageCritical', 'ceng::IConfigBase', CONFIG_DAMAGE_CRITICAL)


def _dump(directory: Path, name: str, base: str, fields) -> None:
    payload = {
        'name': name,
        'base': base,
        'fields': [{'name': field_name, 'raw_type': raw_type, 'type': raw_type,
                    'kind': 'Members', 'default_value': default, 'comment': '',
                    'offset': 0, 'size': 4}
                   for raw_type, field_name, default in fields],
    }
    (directory / f'{name}.json').write_text(json.dumps(payload), encoding='utf-8')


@pytest.fixture
def built_database(data_root: Path, tmp_path: Path) -> Path:
    """A real database, built by stage 1, for the stage 2 tests to read."""
    from noita_db.build import build

    path = tmp_path / 'noita.db'
    build(data_root, path)
    return path


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / 'root'
    root.mkdir()
    _write_docs(root)
    (root / 'common.csv').write_text(COMMON_CSV, encoding='utf-8')
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    lua = root / 'data/scripts/gun/gun_actions.lua'
    lua.parent.mkdir(parents=True, exist_ok=True)
    lua.write_text(GUN_ACTIONS, encoding='utf-8')
    return root

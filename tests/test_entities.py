from pathlib import Path

import pytest

from noita_db import comp_docs
from noita_db.entities import EntityResolver


@pytest.fixture
def resolver(data_root: Path) -> EntityResolver:
    return EntityResolver(data_root, comp_docs.load(data_root / 'comp_docs'))


def instances(entity, component_type):
    return [c for c in entity.components if c.type == component_type]


def attrs(instance) -> dict:
    return dict(instance.attrs)


def test_entity_files_are_found_by_root_tag(resolver: EntityResolver):
    found = resolver.find_entity_files()
    assert 'data/entities/projectiles/light_bullet.xml' in found
    # Lives under data/ and ends in .xml, but its root tag is <Sprite>.
    assert 'data/props_gfx/spark.xml' not in found


def test_base_overwrites_only_named_attributes(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/light_bullet.xml')
    velocity = attrs(instances(entity, 'VelocityComponent')[0])
    assert velocity['gravity_y'] == '200'      # overridden
    assert velocity['air_friction'] == '1.7'   # overridden
    assert velocity['mass'] == '1'             # inherited untouched


def test_base_override_never_adds_a_component(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/light_bullet.xml')
    # The <Base> block patches a SpriteComponent the base does not have, so the
    # only SpriteComponent is the one declared outside the <Base>.
    sprites = instances(entity, 'SpriteComponent')
    assert len(sprites) == 1
    assert attrs(sprites[0]).get('alpha') is None
    assert attrs(sprites[0])['image_file'] == 'data/spark.xml'


def test_component_outside_base_adds_an_instance(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/light_bullet.xml')
    projectiles = instances(entity, 'ProjectileComponent')
    assert len(projectiles) == 2
    inherited, own = projectiles
    assert attrs(inherited)['damage'] == '1'
    assert attrs(own)['damage'] == '0.12'
    assert 'lifetime' not in attrs(own)        # never merged into the inherited one


def test_duplicate_override_tags_match_by_position(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/light_bullet.xml')
    scripts = [attrs(c)['script_source_file'] for c in instances(entity, 'LuaComponent')]
    # The first override removes a.lua; the second replaces b.lua.
    assert scripts == ['data/scripts/replaced.lua']


def test_missing_base_is_ignored(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/fuse.xml')
    assert [(b.file, b.found) for b in entity.bases] == \
        [('data/entities/does_not_exist.xml', False)]
    # The override inside that <Base> contributes nothing.
    assert attrs(instances(entity, 'VelocityComponent')[0]).get('gravity_y') is None


def test_tags_are_bases_first_then_own_deduplicated(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/light_bullet.xml')
    assert entity.tags == ['projectile', 'teleportable_NOT', 'projectile_player']


def test_only_the_first_tags_attribute_is_used(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/trigger.xml')
    assert entity.tags == ['first', 'shared']


def test_unnamed_entity_falls_back_to_the_file_name(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/fuse.xml')
    assert entity.name == 'fuse'
    assert entity.name_key is None


def test_nested_entities_are_kept_as_children(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/fuse.xml')
    assert len(entity.children) == 1
    child = entity.children[0]
    assert child.name_key == '$base_enemy'
    assert child.tags == ['child']


def test_embedded_config_becomes_a_dotted_path(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/light_bullet.xml')
    own = instances(entity, 'ProjectileComponent')[1]
    assert attrs(own)['config_explosion.damage'] == '0.0'
    assert attrs(own)['config_explosion.damage_critical.chance'] == '5'


def test_container_children_are_collected_as_rows(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/fuse.xml')
    inventory = instances(entity, 'MaterialInventoryComponent')[0]
    assert inventory.containers['count_per_material_type'] == [
        {'material': 'oil', 'count': '200'},
        {'material': 'water', 'count': '10'},
    ]


def test_duplicate_attributes_survive_resolution(resolver: EntityResolver):
    entity = resolver.resolve('data/entities/projectiles/fuse.xml')
    velocity = instances(entity, 'VelocityComponent')[0]
    assert [value for name, value in velocity.attrs if name == 'mass'] == ['2', '3', '4']


def test_resolving_a_base_twice_does_not_leak_edits(resolver: EntityResolver):
    resolver.resolve('data/entities/projectiles/light_bullet.xml')
    base = resolver.resolve('data/entities/base_projectile.xml')
    assert attrs(instances(base, 'VelocityComponent')[0])['gravity_y'] == '400'
    assert len(instances(base, 'LuaComponent')) == 2

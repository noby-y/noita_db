from pathlib import Path

from noita_db import comp_docs


def test_scalar_and_composite_expansion(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    area = docs.get('AreaDamageComponent')
    assert set(area.leaves) == {
        'aabb_min.x', 'aabb_min.y',
        'box.min_x', 'box.min_y', 'box.max_x', 'box.max_y',
        'range.min', 'range.max',
        'tint.r', 'tint.g', 'tint.b', 'tint.a',
    }
    assert area.leaves['aabb_min.x'].default == -4.0
    assert area.leaves['range.max'].default == 1.0
    assert area.leaves['tint.a'].default == 1.0


def test_embedded_configs_expand_recursively(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    projectile = docs.get('ProjectileComponent')
    assert projectile.leaves['config_explosion.damage'].default == 5.0
    assert projectile.leaves['config_explosion.physics_explosion_power.min'].default == 0.0
    assert projectile.leaves['config_explosion.damage_critical.damage_multiplier'].default == 2.0
    assert projectile.nested_prefix('config_explosion') == 'config_explosion.'
    assert projectile.nested_prefix('nothing_here') is None


def test_config_structs_are_not_standalone_components(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    assert docs.get('ConfigExplosion').is_component is False
    assert 'ConfigExplosion' not in {doc.name for doc in docs.components}


def test_defaults_and_types(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    projectile = docs.get('ProjectileComponent')
    assert projectile.leaves['lifetime'].default == -1
    assert projectile.leaves['on_collision_die'].default == 1        # bool -> 0/1
    assert projectile.leaves['projectile_type'].default == 'PROJECTILE'
    assert projectile.leaves['projectile_type'].sql_type == comp_docs.TEXT


def test_transform_pseudo_component_defaults_scale_to_one(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    transform = docs.get(comp_docs.TRANSFORM_COMPONENT)
    assert transform.leaves['scale.x'].default == 1.0
    assert transform.leaves['position.x'].default == 0.0


def test_container_fields_get_a_text_column(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    leaf = docs.get('MaterialInventoryComponent').container('count_per_material_type')
    assert leaf is not None and leaf.sql_type == comp_docs.TEXT


def test_coerce_keeps_material_names_on_int_columns(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    leaf = docs.get('PhysicsImageShapeComponent').leaves['material']
    assert comp_docs.coerce('3', leaf) == (3, True)
    # An int at runtime, a material name in the XML: keep the name, flag it.
    assert comp_docs.coerce('wood_prop', leaf) == ('wood_prop', False)


def test_coerce_reads_bare_hex_for_unsigned_fields(data_root: Path):
    docs = comp_docs.load(data_root / 'comp_docs')
    colour = docs.get('SpriteComponent').leaves['color']
    assert comp_docs.coerce('ff20f050', colour) == (0xff20f050, True)
    # Same text on a plain int field is not a colour and stays text.
    material = docs.get('PhysicsImageShapeComponent').leaves['material']
    assert comp_docs.coerce('ff20f050', material) == ('ff20f050', False)

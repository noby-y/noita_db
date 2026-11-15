import json
import sqlite3
from pathlib import Path

import pytest

from noita_db import names
from noita_db.build import MissingInput, build
from noita_db.cli import main
from noita_db.db import SPELL_PROJECTILE_GROUP


@pytest.fixture
def database(data_root: Path, tmp_path: Path):
    build(data_root, tmp_path / 'noita.db')
    connection = sqlite3.connect(tmp_path / 'noita.db')
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def one(connection, sql, *args):
    row = connection.execute(sql, args).fetchone()
    return row[0] if row is not None else None


def test_entity_rows_carry_resolved_names(database):
    row = database.execute(
        "SELECT * FROM entities WHERE source_file = ? AND entity_path = ''",
        ('data/entities/projectiles/light_bullet.xml',)).fetchone()
    assert row['name'] == 'projectile_default'
    assert row['name_key'] == '$projectile_default'
    assert row['display_name'] == 'Default projectile'
    assert row['tags'] == 'projectile,teleportable_NOT,projectile_player'


def test_component_columns_are_typed_and_defaulted(database):
    row = database.execute(
        'SELECT p.* FROM ProjectileComponent p JOIN entities e ON e.id = p.entity_id'
        " WHERE e.source_file = ? AND p.instance_index = 1",
        ('data/entities/projectiles/light_bullet.xml',)).fetchone()
    assert row['damage'] == 0.12
    assert row['lifetime'] == -1                     # default, never set on this instance
    assert row['on_collision_die'] == 1              # documented default "true"
    assert row['projectile_type'] == 'PROJECTILE'
    assert row['config_explosion.explosion_radius'] == 2.0
    assert row['config_explosion.damage_critical.chance'] == 5
    assert row['config_explosion.damage_critical.damage_multiplier'] == 2.0


def test_each_duplicate_component_gets_its_own_row(database):
    rows = database.execute(
        'SELECT instance_index, damage FROM ProjectileComponent p'
        ' JOIN entities e ON e.id = p.entity_id WHERE e.source_file = ?'
        ' ORDER BY instance_index',
        ('data/entities/projectiles/light_bullet.xml',)).fetchall()
    assert [(r['instance_index'], r['damage']) for r in rows] == [(0, 1.0), (1, 0.12)]


def test_repeated_attributes_keep_every_value(database):
    row = database.execute(
        'SELECT mass FROM VelocityComponent v JOIN entities e ON e.id = v.entity_id'
        ' WHERE e.source_file = ?', ('data/entities/projectiles/fuse.xml',)).fetchone()
    assert row['mass'] == 2.0          # the first value lands in the column
    extra = database.execute(
        'SELECT key, value FROM duplicate_attributes ORDER BY key').fetchall()
    assert [(r['key'], r['value']) for r in extra] == [('mass#1', '3'), ('mass#2', '4')]


def test_undocumented_attributes_are_kept_separately(data_root: Path, tmp_path: Path):
    path = data_root / 'data/entities/projectiles/extra.xml'
    path.write_text('<Entity><SpriteComponent nonsense="7"/></Entity>', encoding='utf-8')
    build(data_root, tmp_path / 'x.db')
    connection = sqlite3.connect(tmp_path / 'x.db')
    rows = connection.execute(
        'SELECT component_type, path, value FROM unimplemented_attributes'
        " WHERE path = 'nonsense'").fetchall()
    assert rows == [('SpriteComponent', 'nonsense', '7')]


def test_container_fields_are_stored_as_json(database):
    value = one(database,
                'SELECT count_per_material_type FROM MaterialInventoryComponent')
    assert json.loads(value) == [{'material': 'oil', 'count': '200'},
                                 {'material': 'water', 'count': '10'}]


def test_nested_entities_become_child_rows(database):
    child = database.execute(
        'SELECT * FROM entities WHERE parent_id IS NOT NULL').fetchone()
    assert child['entity_path'] == '0'
    assert child['name_key'] == '$base_enemy'
    assert child['source_file'] == 'data/entities/projectiles/fuse.xml'


def test_missing_base_is_recorded_but_not_fatal(database):
    row = database.execute(
        'SELECT base_file, found FROM entity_bases WHERE found = 0').fetchone()
    assert row['base_file'] == 'data/entities/does_not_exist.xml'
    assert one(database, "SELECT count(*) FROM parse_warnings"
                         " WHERE message LIKE 'missing base%'") == 1


def test_spells_resolve_names_and_link_to_entities(database):
    row = database.execute(
        "SELECT * FROM spells WHERE action_id = 'LIGHT_BULLET'").fetchone()
    assert row['display_name'] == 'Spark bolt'
    assert row['display_description'] == 'A weak, sparkling projectile'
    assert row['mana'] == 5
    link = database.execute(
        'SELECT l.entity_file, l.entity_id FROM spell_projectile_links l'
        ' JOIN spells s ON s.id = l.spell_id'
        " WHERE s.action_id = 'LIGHT_BULLET'").fetchone()
    assert link['entity_file'] == 'data/entities/projectiles/light_bullet.xml'
    assert one(database, 'SELECT source_file FROM entities WHERE id = ?',
               link['entity_id']) == 'data/entities/projectiles/light_bullet.xml'


def test_effects_are_searchable_by_value(database):
    found = database.execute(
        'SELECT s.action_id FROM spell_effects e JOIN spells s ON s.id = e.spell_id'
        " WHERE e.key = 'screenshake' AND e.op = 'add' AND e.value_num = 10").fetchall()
    assert [r['action_id'] for r in found] == ['LIGHT_BULLET']


def test_extra_entity_links_resolve_to_entities(database):
    rows = database.execute(
        'SELECT entity_file, entity_id FROM spell_extra_entity_links'
        ' ORDER BY id').fetchall()
    assert [r['entity_file'] for r in rows] == ['data/entities/misc/homing.xml',
                                                'data/entities/particles/spark.xml']
    assert all(r['entity_id'] is not None for r in rows)


def test_translations_are_stored_for_the_chosen_languages(database):
    assert one(database, 'SELECT text FROM translations WHERE code_name = ?'
                         " AND language = 'English'", 'action_homing') == 'Homing'
    assert one(database, "SELECT count(*) FROM translations"
                         " WHERE language != 'English'") == 0


def test_extra_languages_can_be_imported(data_root: Path, tmp_path: Path):
    build(data_root, tmp_path / 'ru.db', languages=('English', 'русский'))
    connection = sqlite3.connect(tmp_path / 'ru.db')
    assert connection.execute(
        'SELECT count(*) FROM translations').fetchone()[0] >= 8


def add_favourites(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    group_id = connection.execute(
        "INSERT INTO entity_groups (name) VALUES ('favourites')"
        ' RETURNING id').fetchone()[0]
    connection.execute(
        'INSERT INTO entity_group_members (group_id, entity_key) VALUES (?, ?)',
        (group_id, 'data/entities/projectiles/light_bullet.xml#'))
    connection.commit()
    connection.close()


def test_rebuild_drops_groups_unless_asked(data_root: Path, tmp_path: Path):
    db_path = tmp_path / 'noita.db'
    build(data_root, db_path)
    add_favourites(db_path)

    build(data_root, db_path)
    connection = sqlite3.connect(db_path)
    # Only the derived group is left; the hand-made one went with the rebuild.
    assert [r[0] for r in connection.execute('SELECT name FROM entity_groups')] == \
        [SPELL_PROJECTILE_GROUP]
    connection.close()


def test_preserve_groups_keeps_membership_across_a_rebuild(data_root: Path, tmp_path: Path):
    db_path = tmp_path / 'noita.db'
    build(data_root, db_path)
    add_favourites(db_path)

    build(data_root, db_path, preserve_groups=True)
    connection = sqlite3.connect(db_path)
    # The key still points at a live entity even though ids were renumbered.
    row = connection.execute(
        'SELECT e.name FROM entity_group_members m'
        ' JOIN entities e ON e.entity_key = m.entity_key'
        ' JOIN entity_groups g ON g.id = m.group_id'
        " WHERE g.name = 'favourites'").fetchone()
    assert row == ('projectile_default',)
    connection.close()


def test_spell_projectile_group_is_built_from_the_links(database):
    rows = database.execute(
        'SELECT e.source_file FROM entity_group_members m'
        ' JOIN entities e ON e.entity_key = m.entity_key'
        ' JOIN entity_groups g ON g.id = m.group_id'
        ' WHERE g.name = ? ORDER BY e.source_file',
        (SPELL_PROJECTILE_GROUP,)).fetchall()
    linked = database.execute(
        'SELECT DISTINCT entity_file FROM spell_projectile_links'
        ' WHERE entity_id IS NOT NULL ORDER BY entity_file').fetchall()
    assert [r[0] for r in rows] == [r[0] for r in linked]
    assert rows                                  # the fixture data does link some


def test_spell_projectile_group_is_regenerated_not_appended(data_root: Path, tmp_path: Path):
    db_path = tmp_path / 'noita.db'
    build(data_root, db_path)
    connection = sqlite3.connect(db_path)
    group_id, before = connection.execute(
        'SELECT g.id, count(*) FROM entity_groups g JOIN entity_group_members m'
        ' ON m.group_id = g.id WHERE g.name = ? GROUP BY g.id',
        (SPELL_PROJECTILE_GROUP,)).fetchone()
    # A key from an older game version, the kind --preserve-groups would carry.
    connection.execute(
        'INSERT INTO entity_group_members (group_id, entity_key) VALUES (?, ?)',
        (group_id, 'data/entities/projectiles/gone.xml#'))
    connection.commit()
    connection.close()

    build(data_root, db_path, preserve_groups=True)
    connection = sqlite3.connect(db_path)
    assert one(connection, 'SELECT count(*) FROM entity_groups WHERE name = ?',
               SPELL_PROJECTILE_GROUP) == 1
    assert one(connection,
               'SELECT count(*) FROM entity_group_members m JOIN entity_groups g'
               ' ON g.id = m.group_id WHERE g.name = ?',
               SPELL_PROJECTILE_GROUP) == before
    connection.close()


def test_build_rejects_an_incomplete_root(tmp_path: Path):
    with pytest.raises(MissingInput):
        build(tmp_path, tmp_path / 'out.db')


def test_cli_reports_a_missing_root(tmp_path: Path, capsys):
    assert main([str(tmp_path), '-o', str(tmp_path / 'out.db'), '-q']) == 2
    assert 'missing under' in capsys.readouterr().err


def test_cli_builds(data_root: Path, tmp_path: Path):
    out = tmp_path / 'cli.db'
    assert main([str(data_root), '-o', str(out), '-q']) == 0
    connection = sqlite3.connect(out)
    assert connection.execute('SELECT count(*) FROM entities').fetchone()[0] > 0
    assert connection.execute("SELECT value FROM build_info WHERE key = 'spells'"
                              ).fetchone()[0] == '3'
    connection.close()


def test_translation_lookup_strips_the_dollar(data_root: Path):
    table = names.load(data_root / 'data/translations/common.csv')
    assert table.lookup('$action_homing') == 'Homing'
    assert table.lookup('action_homing') == 'Homing'
    assert table.lookup(None) is None
    # A quoted field containing a comma stays one value.
    assert table.lookup('$actiondesc_light_bullet') == 'A weak, sparkling projectile'


def test_the_language_code_row_is_not_mistaken_for_data(data_root: Path):
    """The row above the names holds codes, not translations.

    Reading it as the header would offer `en` and the file's `NOTES` and
    `max length` columns as languages, and leave `current_language` looking
    like a translatable string.
    """
    table = names.load(data_root / 'data/translations/common.csv')
    assert table.lookup('current_language') is None
    assert table.entries.keys().isdisjoint(
        {('action_homing', 'en'), ('action_homing', 'max length')})


def test_a_file_already_cut_down_to_the_names_row_still_loads(tmp_path: Path):
    """What the old "copy it to the root" setup left behind."""
    path = tmp_path / 'common.csv'
    path.write_text('current_language,English\naction_homing,Homing\n',
                    encoding='utf-8')
    assert names.load(path).lookup('$action_homing') == 'Homing'

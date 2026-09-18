# noita-db

Two programs over one SQLite file. `noita-db` parses Noita's entity XML,
`comp_docs/`, `common.csv` and `gun_actions.lua` into it and does nothing else.
`noita-tui` reads and writes that file and opens nothing else.

```
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/noita-db . -o noita.db          # ~6s over the full data/ tree
.venv/bin/noita-tui noita.db
.venv/bin/python -m pytest
```

# Stage 1 — the CLI

```
noita-db [root] -o noita.db [--preserve-groups] [--languages English,...] [-q]
```

`root` is the directory holding `data/`, `comp_docs/` and `common.csv`.
Every run rebuilds from scratch. `--preserve-groups` is the one exception: it
carries `entity_groups` / `entity_group_members` over from the database at
`-o`. Membership is stored by `entity_key`, not by row id, so it survives the
renumbering.

## Layout

| module | job |
| --- | --- |
| `xml_tolerant.py` | forgiving XML reader (see below) |
| `comp_docs.py` | component schema: field → column expansion, defaults, coercion |
| `entities.py` | file discovery, `<Base>` resolution, tag merging |
| `names.py` | `common.csv` |
| `spells.py` | `gun_actions.lua` via a real Lua AST |
| `db.py` | schema + writer |
| `build.py`, `cli.py` | orchestration and the command line |

## Schema

`SCHEMA.md` is the full reference, written for stage 2: every table, the
query shapes a filter UI needs, and the traps. The short version follows.

Component types are a fixed set, so each gets its own table with typed columns.
Column names are the XML attribute paths verbatim, dots included, which means
they need quoting:

```sql
SELECT e.name, p.damage, p."config_explosion.damage"
FROM ProjectileComponent p JOIN entities e ON e.id = p.entity_id
WHERE p."config_explosion.explosion_radius" > 20;
```

Field types expand to the suffixes the XML actually writes: `vec2` → `.x/.y`,
`ValueRange` → `.min/.max`, `aabb` → `.min_x/.min_y/.max_x/.max_y`, `fcolor` →
`.r/.g/.b/.a`, `xform` → `position.x/…/scale.y`. Embedded config structs expand
recursively under their own name (`config_explosion.damage_critical.chance`).
Repeating fields such as `count_per_material_type` are stored as a JSON array.

Core tables:

- `entities` — one row per entity, including nested `<Entity>` children
  (`parent_id`, `entity_path`). `entity_key` is `source_file#entity_path`.
- `entity_tags`, `entity_bases` — tags in resolved order; base files with a
  `found` flag.
- `components` — the index of every instance: type, `instance_index`,
  `_enabled`, `_tags`. Each typed table repeats `entity_id` and
  `instance_index` so it stands alone.
- `unimplemented_attributes` — attributes with no `comp_docs` field.
- `duplicate_attributes` — the 2nd and later values of a repeated attribute,
  keyed `name#1`, `name#2`.
- `parse_warnings` — missing base files, values that did not match their type.
- `translations` — `(code_name, language, text)`, English only unless
  `--languages` says otherwise.
- `spells`, `spell_effects`, `spell_projectile_links`,
  `spell_extra_entity_links`, `spell_related_entities`.
- `entity_groups`, `entity_group_members` — written by the GUI, not the CLI.

`spells` columns are derived from the keys `gun_actions.lua` actually uses, so
a new key in a future game version becomes a column rather than a loss.

`spell_effects` decomposes each write in an `action` body into
`(scope, key, op, value_num | value_text)`, so `c.screenshake = c.screenshake +
10.0` is findable by value:

```sql
SELECT s.display_name, e.op, e.value_num
FROM spell_effects e JOIN spells s ON s.id = e.spell_id
WHERE e.key = 'screenshake' AND e.value_num >= 10;
```

`op` is one of `set/add/mul/concat/min/max/expr`. There is no `sub`: a
subtraction is an `add` of a negative `value_num`, so `value_num < 0` finds
every write that lowers a key. `expr` means the value depends on runtime state
(12 of ~1000 writes) and the Lua text is in `value_text`. `conditional = 1`
means the write sits inside an `if` or a loop.

In `spell_projectile_links`, `entity_file` holds the Lua expression as written
when the `add_projectile*` argument is a variable rather than a literal path;
`entity_id` is then null.

# Stage 2 — the TUI

```
noita-tui [noita.db]
```

Every view is a query against the open database, run on each keystroke. The
component types, their attributes, the tags and the values offered for
completion all come out of the file, so a rebuild against a patched game
version exposes the new fields without a code change. Nothing is cached except
the `SELECT DISTINCT` behind value completion.

`f9` switches between the two views: entities, and the 422 spells out of
`gun_actions.lua`.

| module | job |
| --- | --- |
| `db.py` | opening the file, refusing one from a future schema, `quote()` |
| `catalog.py` | component types with rows, their columns, declared types, `typeof`; the spell columns, effect scopes and keys |
| `query.py` | entity filters → one `SELECT`: bindings, combinator, scopes |
| `spell_query.py` | spell filters → one `SELECT` over `spells` |
| `groups.py` | the two tables stage 2 writes |
| `detail.py`, `spell_detail.py` | one entity, or one spell, read whole |
| `widgets.py` | completing input, prompt and confirm modals |
| `filter_row.py`, `spell_row.py` | a filter as a row of fields |
| `app.py` | the two views and the keys |

**Filters.** One row is one predicate: component type, instance binding,
attribute, operator (`= != < <= > >= contains`), value. Leave the attribute
blank for a bare "has this component", which goes through `components` and so
works for a type a future patch leaves undocumented. A half-typed row is
outlined in red and sits out of the query rather than breaking it.

**Bindings.** Filters sharing a binding number must be satisfied by the same
component instance; different numbers must be satisfied by different ones, so
`SpriteComponent #1 z_index < 0` with `SpriteComponent #2 z_index > 0` finds
the 105 entities with a sprite behind them and another in front. New rows join
`#1`, because one component satisfying two conditions is the ordinary case.
Distinctness only applies under `AND`: under `OR` and `XOR` each binding is an
independent `EXISTS`, and `XOR` means exactly one, not an odd number.

**Scopes** — group, tag and text search — are always ANDed, whatever the
combinator says. A group as a term would let `OR` quietly pull in entities from
outside it.

**Groups.** The current group is both what `+entity` / `+all` write to and,
once `scope` is on, what the result is restricted to. Creating a group does not
scope to it: it is empty at that moment, and scoping would hide the result
about to go into it. Membership is stored by `entity_key`, so
`noita-db --preserve-groups` carries it across a rebuild; a key that no longer
matches an entity is listed as an orphan rather than dropped.

## The spells view

`f9`. Same machinery, a different set of tables, and no instance binding — a
spell's writes accumulate rather than compete, so every filter is its own
`EXISTS` and two filters on `c.fire_rate_wait` may be satisfied by two
different writes. A filter row is **source**, **key**, operator, value:

| source | asks about | example |
| --- | --- | --- |
| `spell` | a column of the spells row | `spell.mana <= 20` |
| `c`, `shot_effects`, `global` | a write in the `action` body | `c.screenshake >= 10` |
| `effect` | the raw `spell_effects` row | `effect.op = mul`, `effect.conditional = 1` |
| `projectile`, `extra_entity`, `related` | an entity the spell spawns | `projectile.entity_file contains bomb` |

`c.screenshake = c.screenshake + 10.0` is stored as `add 10.0`, so
`c.screenshake = 10` finds it: the operator compares the value written, and
`effect.op` is where you say *how* it was written. There is no `sub` — a
subtraction is an `add` of a negative number, so `c.fire_rate_wait < 0` is the
48 spells that fire faster. An empty value means "this is set at all", which
gives you `c.screenshake` for every spell that shakes the screen, and
`spell.max_uses` for every spell that is limited.

The detail pane shows a spell's properties, every write in source order with
its op and whether it sits inside an `if`, and its entity links resolved to
entity names — including the ones that resolve to nothing, because 12
`add_projectile` calls take a Lua expression rather than a literal path.

Groups are entity-only, so `f2`–`f8` say so rather than doing something
surprising on this view.

| key | |
| --- | --- |
| `f9` | entities ⇄ spells |
| `ctrl+n` / `ctrl+r` | add or drop a filter row |
| `ctrl+t` | cycle `AND` → `OR` → `XOR` (per view) |
| `f2` / `f3` | create or delete a group |
| `f4` / `f6` | add or remove the highlighted entity |
| `f5` | add every entity in the current result, in one statement |
| `f7` / `f8` | scope to the current group / clear group, tag and search |
| `↓` `↑` `tab` | walk and accept a completion |

**The material trap, surfaced.** ~2180 attributes are INTEGER columns holding a
material name, and SQLite sorts every number before every text value. Point an
ordering operator at one and the status line says so:
`PhysicsImageShapeComponent.material holds text in 546 of 546 rows — < skips
those`. Equality against those columns takes the name (`= 'wood_prop'`) and
works; the value field completes from the column's distinct values.

## Why the XML reader is hand-written

460 of the 4503 XML files under `data/` are rejected by a conforming parser,
and every one of those failures is load-bearing:

- 196 set the same attribute twice — exactly the case the plan wants preserved.
- 263 put a `<!-- … -->` between attributes inside an open tag, or use `--`
  inside comment text.
- `data/entities/misc/eradicate.xml` ends with `<Entity>` instead of `</Entity>`.

`xml_tolerant` accepts all of it and keeps attributes as an ordered list so
duplicates stay distinguishable.

## Decisions worth knowing

**`_remove_from_base`.** Not in the plan's rule list, but Noita honours it and
`mimic_potion.xml` / `boss_sky.xml` depend on it: an override carrying
`_remove_from_base="1"` deletes the matched inherited instance instead of
merging into it. Without this, `mimic_potion` keeps a `ProjectileComponent` and
two `LuaComponent`s it explicitly disowns.

**Positional override matching.** The plan says an override targets "the
matching component type", which is ambiguous when the base has several. The
data settles it: `mimic_potion.xml` has two consecutive
`<LuaComponent _remove_from_base="1"/>` tags removing two *different* inherited
LuaComponents. So the k-th override of a type matches the k-th inherited
instance, and positions are fixed before any removal is applied.

**Nested `<Entity>` elements.** 488 files nest child entities, which the plan
does not mention. They are stored as real entity rows with `parent_id` set
rather than dropped. `source_file` is the file being built; `defined_in` is
where the element was written, which differs when a child entity arrives
through a `<Base>`.

**Material names in int columns.** ~2200 attributes are ints at runtime but
name a material or a herd in XML (`material="wood_prop"`, `herd_id="orcs"`),
because the engine resolves the name on load. SQLite columns carry affinity
rather than a hard type, so the name is stored as-is and a row is written to
`parse_warnings`. Nothing is lost, and `SELECT … WHERE material = 'wood_prop'`
works. `uint32` colour fields written as bare hex (`ff20f050`) are converted.

**What lands in `unimplemented_attributes`** (743 rows): attributes with no
`comp_docs` field, e.g. `config_explosion.hole_image`,
`SpriteOffsetAnimatorComponent.offset_original.x`, and
`InheritTransformComponent`'s `only_position` when it is written inside the
`<Transform>` child instead of on the component.

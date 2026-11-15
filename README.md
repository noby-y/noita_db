# noita-db

Noita's entity and spell files, in one SQLite database you can query.

Two commands. `noita-db` reads the game's XML, component docs, translations and
`gun_actions.lua` and writes a `.db` file. `noita-tui` is a terminal browser over
that file, so you can ask things like "every entity with a projectile that
explodes for more than 20 radius" without writing SQL.

Against the shipped game data you get 3776 entities, 30227 components across 138
component types, 274 tags, 422 spells and 3607 English strings.

## Setup

You need Python 3.11 or newer and a copy of Noita's game files.

```sh
python -m venv .venv
.venv/bin/pip install -e '.[tui]'
```

The `tui` extra pulls in Textual. Without it you still get `noita-db` and can
query the database with any SQLite client.

### Supplying the game data

`comp_docs/` ships with this repo. The rest you must add manually.

[unpack `Noita/data/data.wak`](https://noita.wiki.gg/wiki/Modding#Extracting_data_files)
inside of the project directory, such that it looks like this:
```
data/
noita_db/
noita_tui/
...
```

1. Unpack `Noita/data/data.wak` into this directory, so that `data/entities/`
   and `data/scripts/` exist. [noita_wak_tool](https://github.com/dextercd/Noita-WAK-Tools)
   or any of the community unpackers will do it.
2. Copy `data/translations/common.csv` up to the repo root.

`noita-db` checks for `data/`, `comp_docs/`, `common.csv` and
`data/scripts/gun/gun_actions.lua` before it starts, and names whatever is
missing.

## Building the database

```sh
.venv/bin/noita-db . -o noita.db
```

Takes about six seconds over the full `data/` tree on my machine. Progress goes to stderr;
`-q` silences everything but errors.

| flag | |
| --- | --- |
| `root` | directory holding `data/`, `comp_docs/` and `common.csv` (default `.`) |
| `-o`, `--output` | database to write (default `noita.db`) |
| `--preserve-groups` | keep the entity groups already in the database at `-o` |
| `--languages` | which `common.csv` columns to import (default `English`) |
| `-q`, `--quiet` | errors only |

You can rebuild it if there are any issues with the db. `--preserve-groups` will save
any custom entity groups after rebuild.

`--languages` takes the column headings from `common.csv` verbatim:
`--languages English,Deutsch,日本語`.

## Browsing it

```sh
.venv/bin/noita-tui noita.db
```

`f9` switches between the entity view and the spell view. `ctrl+q` quits. Every
view is a live query, so results update as you type, and the component types,
attributes, tags and values all complete from whatever is in the file.

### Filters

`ctrl+n` adds a filter row, `ctrl+r` drops one. A row is five fields: component
type, instance number, attribute, operator, value.

```
ProjectileComponent  #1  config_explosion.explosion_radius  >  20
```

Operators are `=`, `!=`, `<`, `<=`, `>`, `>=` and `contains`. Leave the
attribute blank to ask only whether the entity has that component at all.

A half-finished row is outlined in red and sits out of the query instead of
breaking it, so you can leave a row parked while you work on another.

`ctrl+t` cycles how rows combine: `AND`, then `OR`, then `XOR`. Each view
remembers its own setting.

### Asking about nothing

Quote a value and it is matched literally, which is how you ask for an empty
field. `ProjectileComponent.muzzle_flash_file = ""` is the 423 projectiles with
no muzzle flash, and `!= ""` is the ones that have one.

An *unquoted* empty field means something different: that row is still being
typed, so it stays out of the query. That distinction is the one thing about the
filter syntax worth remembering.

Empty covers both the empty string and NULL, so `mWhoShot = ""` is every entity
carrying a projectile. Quotes work on ordinary values too (`= "data/spark.xml"`).
`""` only makes sense with `=` or `!=`; point `<` at it and the status line says
so.

### Instance numbers

Rows sharing a number must be satisfied by the same component instance, and
different numbers must be satisfied by different ones. So

```
SpriteComponent  #1  z_index  <  0
SpriteComponent  #2  z_index  >  0
```

finds the 105 entities with one sprite behind them and another in front. New
rows start at `#1`, since one component meeting both conditions is the usual
case.

This only applies under `AND`. Under `OR` and `XOR` each number is tested
independently, and `XOR` means exactly one matched, not an odd count.

### Search, tags and groups

The search box matches name, display name or file. The tag box takes one tag.
Both are always ANDed with your filters, whatever the combinator says.

Groups are saved sets of entities, useful for building up a list across several
queries.

| key | |
| --- | --- |
| `f2` / `f3` | create or delete a group |
| `f4` / `f6` | add or remove the highlighted entity |
| `f5` | add every entity in the current result |
| `f7` | restrict the result to the current group |
| `f8` | clear group, tag and search |

Creating a group does not scope to it, because it is empty at that moment and
scoping would hide the result you are about to put in it. Two groups come
prebuilt: `spell projectiles`, the 199 entities that spells fire, rewritten on
every build, and `bosses`.

Groups are entity-only. On the spell view `f2` to `f8` tell you so rather than
doing something surprising.

### Export

`f10` writes the current result as one entity path per line, in the form the
game files use:

```
data/entities/projectiles/fuse.xml
data/entities/animals/acidshooter.xml#0
```

The `#0` marks a nested `<Entity>`. This is what a mod, a `<Base file="...">` or
a `grep -f` wants. It exports everything the filters matched, not just the rows
scrolled into view, and asks before overwriting an existing file.

### The spell view

`f9`. A spell filter row is source, key, operator, value, with no instance
number, since a spell's effects add up rather than compete.

| source | asks about | example |
| --- | --- | --- |
| `spell` | a property of the spell itself | `spell.mana <= 20` |
| `c`, `shot_effects`, `global` | something the spell writes | `c.screenshake >= 10` |
| `effect` | how it was written | `effect.op = mul` |
| `projectile`, `extra_entity`, `related` | an entity the spell spawns | `projectile.entity_file contains bomb` |

The operator compares the value written, not the result. `c.screenshake =
c.screenshake + 10.0` is stored as an add of `10.0`, so `c.screenshake = 10`
finds it, and `effect.op` is where you say how it was written. There is no
`sub`: a subtraction is an add of a negative number, so `c.fire_rate_wait < 0`
is the 48 spells that fire faster.

An empty value asks whether the key is touched at all, which makes
`c.screenshake` every spell that shakes the screen and `spell.max_uses` every
spell with limited charges. `""` asks the opposite, as on the entity view.

The detail pane shows a spell's properties, every write in source order, and its
spawned entities resolved to names.

### One trap the TUI warns you about

A couple of thousand attributes are integer columns that actually hold a material
name, because the engine resolves names at load time. SQLite sorts every number before
every text value, so an ordering operator on one of those columns quietly skips
the rows you want. Point `<` at one and the status line tells you:

```
PhysicsImageShapeComponent.material holds text in 546 of 546 rows - < skips those
```

Equality works fine and takes the name: `material = wood_prop`. The value field
completes from the column's real values.

## Querying the database directly

It is an ordinary SQLite file, so `sqlite3 noita.db` and any client or library
works. Two things to know.

Each component type has its own table, and the column names are the XML
attribute paths verbatim, dots included, so they need quoting:

```sql
SELECT e.name, p.damage, p."config_explosion.damage"
FROM ProjectileComponent p JOIN entities e ON e.id = p.entity_id
WHERE p."config_explosion.explosion_radius" > 20;
```

Compound fields expand into suffixed columns: a vec2 becomes `.x` and `.y`, a
range becomes `.min` and `.max`, a colour becomes `.r/.g/.b/.a`, and nested
config structs nest too (`config_explosion.damage_critical.chance`).

Spell effects are decomposed one row per write, so you can search by value:

```sql
SELECT s.display_name, e.op, e.value_num
FROM spell_effects e JOIN spells s ON s.id = e.spell_id
WHERE e.key = 'screenshake' AND e.value_num >= 10;
```

Beyond that, `.tables` and `.schema entities` in the sqlite3 shell are the
fastest way around. The tables you will reach for first are `entities`,
`entity_tags`, `components`, `translations`, `spells` and `spell_effects`.

## Tests

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

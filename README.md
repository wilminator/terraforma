# TerraForma

The TerraForma RPG Engine runs browser RPGs. A game is a module: its content (seed data), assets and rules settings, plugged into the engine. The engine provides the rest:
- accounts and security;
- the server calls and live fights over WebSockets;
- the database layer on Postgres, MySQL or SQLite;
- a map-ready world.

[Vanguard Tavern](https://github.com/wilminator/terraforma-vanguard-tavern) is the first game built on it. [terraforma-example-game](https://github.com/wilminator/terraforma-example-game) shows how to build your own.

## License

The engine is licensed under the GNU AGPL version 3 (`LICENSE`), with an additional permission (`LICENSE-EXCEPTION.md`). Changes to the engine stay open, even when it only runs on a server. A game module that uses the engine through its public interfaces may be licensed however its author likes.

Copyright (C) 2026 Michael Allen Wilmes.

## Making a game

A game is a Python package that hands the engine a `Game` and builds the app:

```python
from terraforma.app import create_app
from terraforma.game import Game
from terraforma.settings import load_settings

GAME = Game(name="My Game", seed_dir=..., assets_dir=...)

def app():
    return create_app(load_settings(), GAME)
```

To serve it, run `python -m terraforma serve my_game:app`. It checks the settings, brings the database up to date, then serves.

For tests, add `pytest_plugins = ["terraforma.testing"]` to the game's `tests/conftest.py`. Override the `game` fixture there, and `app_client` serves your game on every database under test.

## Running it

Everything runs in Docker.

```sh
cp settings.example.toml settings.toml      # then set session_secret
mkdir -m 700 keys
docker compose run --rm app python -m terraforma keys new   # the encryption key (once)
docker compose up app                       # the engine alone, http://localhost:8000 (Postgres)
docker compose run --rm test                # every test, on SQLite, Postgres and MySQL
```

Settings come from `settings.toml`, not environment variables. The NAS the game runs on may not pass environment variables or Docker secrets into the container. `python -m terraforma check-settings` says what's wrong with the file, if anything.

On start, the container checks the settings, brings the database up to date (`python -m terraforma serve`), then serves. `GET /api/health` answers the container's health check.

## Encryption keys

Secrets the engine must read back, such as 2FA secrets, are stored encrypted with a key kept in files beside `settings.toml` (the `key_dir` setting), never in the database. There are two slots: `current.key` encrypts, and `previous.key` still decrypts what the last key made, so a rotation never locks anyone out.

```sh
python -m terraforma keys new       # the first key; refuses to replace one
python -m terraforma keys rotate    # a new key, and everything stored is re-encrypted with it
```

The server won't start without a current key. Back the keys up apart from the database: a database backup is unreadable without them, and both together give everything away. A column that holds encrypted values is registered with `terraforma.keys.encrypted_column`, so rotating finds it.

## Changing the tables

Change the models, then make a migration and read it over, since autogenerate misses renames:

```sh
alembic revision --autogenerate -m "what changed"
```

A test fails whenever the models and the migrations disagree.

## Seed data

A game's starting content is JSON in its `seed/` folder (`Game(seed_dir=...)`): one file per kind of content, each a list of objects. These formats are a public interface, so changing one is deliberate and noted here.

| File | A row has |
|---|---|
| `abilities.json` | `key`, `name`, `kind` (`spell` or `skill`), `mp_cost`, `description`, `icon`, `effect`, `presentation` |
| `items.json` | `key`, `name`, `price`, `one_use`, `description`, `icon`, `use_effect`, `equip_slots`, `stat_bonus`, `stat_percent`, `attack`, `use_presentation`, `fight_presentation` |
| `jobs.json` | `key`, `name`, `xp_needed`, `stat_growth` (stats per level), `abilities` (each `{"ability": key, "level": n}`, the level a hero gets it at; a bare key means level 1) |
| `personalities.json` | `key`, `name`, an animation for each of `base equip flee hit die attack_close attack_throw attack_shoot skill spell item`, and `overworld` (`stand` and `move`, each facing `up down left right`) |
| `monsters.json` | `key`, `name`, `personality` (key), `xp_reward`, `gold_reward`, `stats`, `abilities`, `items`, `equipment` (keys), `ai` |

- **Keys.** Every row has a `key` (1-64 lowercase letters, digits, `_` or `-`) that the game picks and never reuses. Rows name each other by key. Only `key` and `name` are required; the rest have defaults.
- **Equipment slots.** An item's `equip_slots` lists the slots it takes. The engine's slots are `rhand rammo rarm lhand lammo larm body head back feet`; `hand`, `ammo` and `arm` are *sided* (the player picks left or right when equipping), and a two-handed weapon lists `lhand` and `rhand`. A weapon's `attack.ammo_type` has to match its ammunition's. Gear never stacks; ammunition and non-equipment stack to 250, in at most 12 stacks (for now these limits are constants in `heroes/inventory.py`).
- **Stats** are `HP MP Speed Accuracy Strength Dodge Block Power Resistance Focus`; a stat left out is 0, and any other name is refused.
- **An effect** is `{"effect", "targets", "base", "added", "attribute"}`: `effect` is one of `none heal hurt revive slay increase_stats decrease_stats steal_stats cause_good_status remove_good_status cause_bad_status remove_bad_status restore_mp`, `targets` one of `individual group party all_parties all_enemies all_allies`, and `attribute` is the game's own kind of damage.
- **An animation** is `{"animation", "images", "sounds", "times"}`; pictures and sounds are plain file names under the game's assets folder (no absolute paths, no `..`).
- **Checking.** Anything unknown, mistyped or out of range is refused with the file, row and field named, and so is a repeated key or a reference to a key that doesn't exist. All problems are listed at once, and the server won't start on a bad seed.
- **Loading.** On every start the engine makes the database match the seed: new keys are added and known keys updated in place. A key the seed no longer lists is kept but marked inactive, never deleted, because heroes and fight history may still point at it. A file the seed doesn't have leaves its table alone.

`terraforma.seed.load_seed` reads the files; `terraforma.content.schema.check_seed` checks them; `terraforma.content.loader.load_content` loads them.

## Databases

The engine is database-agnostic. Postgres is the primary database, MySQL is supported, and SQLite serves tests and development. To keep it that way:

1. Tables are SQLAlchemy models only, and Alembic writes the schema changes.
2. Only generic column types are used, never database-specific ones.
3. Structured data goes in JSON columns, never serialized language objects.
4. Anything that really differs between databases lives in `terraforma/db/dialect.py`, and nowhere else.
5. There is no raw SQL outside that module and the migrations.
6. Every database test runs on all three databases.

## Map-ready from the start

Maps and a changing world come later. The groundwork is there from the first commit:

- **Location.** Everything that exists somewhere has a map and a tile (`Located`).
- **Randomness.** All of it comes from the world's seed, through named streams (`WorldRng`), so worlds can be rebuilt and bugs replayed.
- **Time.** The world keeps its own clock, counted in ticks rather than wall time (`advance_clock`).
- **Checked input.** Map names and coordinates from the browser are validated like any other input (`Position`).

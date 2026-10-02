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
| `statuses.json` | `key`, `name`, `kind` (`good` or `bad`), `description`, `icon`, `duration`, `intensity`, `ticks`, `modifiers`, `xp_share` (see Statuses below) |

- **Keys.** Every row has a `key` (1-64 lowercase letters, digits, `_` or `-`) that the game picks and never reuses. Rows name each other by key. Only `key` and `name` are required; the rest have defaults.
- **Equipment slots.** An item's `equip_slots` lists the slots it takes. The engine's slots are `rhand rammo rarm lhand lammo larm body head back feet`; `hand`, `ammo` and `arm` are *sided* (the player picks left or right when equipping), and a two-handed weapon lists `lhand` and `rhand`. A weapon's `attack.ammo_type` has to match its ammunition's. Gear never stacks; ammunition and non-equipment stack to 250, in at most 12 stacks (for now these limits are constants in `heroes/inventory.py`).
- **Stats** are the game's `Rules.stats` (see Fight rules below), by default `HP MP Speed Accuracy Strength Dodge Block Power Resistance Focus`; a stat left out is 0, and any other name is refused.
- **An effect** is `{"effect", "targets", "base", "added", "attribute", "stats", "status", "duration"}`: `effect` is one of `none heal hurt revive slay increase_stats decrease_stats steal_stats cause_good_status remove_good_status cause_bad_status remove_bad_status restore_mp`, `targets` is `individual`, `group`, `party`, `all_parties` (every party, the actor's own included), `all_allies`, `all_enemies`, `all_not_allies` (enemies and neutrals), `all_not_enemies` (allies and neutrals), or a number *n* (the target and *n* neighbours each way along its group, hit less the further they are), and `attribute` is the game's own kind of damage. `slay`'s `base` is its chance out of 100. The stat effects (`increase_stats`, `decrease_stats`, `steal_stats`) need `stats`, the non-resource stats they push (`base` and `added` are the amount); `cause_good_status` and `cause_bad_status` need a `status` (a key in `statuses.json` of the matching kind) and may set `duration` (rounds) over the status's own; `remove_good_status` and `remove_bad_status` take off the one `status` names, or every status of their kind if it is left out.
- **An animation** is `{"animation", "images", "sounds", "times"}`; pictures and sounds are plain file names under the game's assets folder (no absolute paths, no `..`).
- **Checking.** Anything unknown, mistyped or out of range is refused with the file, row and field named, and so is a repeated key or a reference to a key that doesn't exist. All problems are listed at once, and the server won't start on a bad seed.
- **Loading.** On every start the engine makes the database match the seed: new keys are added and known keys updated in place. A key the seed no longer lists is kept but marked inactive, never deleted, because heroes and fight history may still point at it. A file the seed doesn't have leaves its table alone.

`terraforma.seed.load_seed` reads the files; `terraforma.content.schema.check_seed` checks them; `terraforma.content.loader.load_content` loads them.

## Statuses

A status is something a fighter can be under, and `statuses.json` is where a game defines them. A fighter under one carries a *token* that records the status, **who placed it** (the *source*), how many rounds have gone by and how long it lasts. There are two mechanisms, and a game can use either or both:

1. **Status tokens.** A status lists *ticks* and *modifiers*:
   - A **tick** is `{"when", "action", "every", "amount", "percent", "resource", "attribute", "chance"}`. `when` is `round_start`, `round_end`, `turn_start`, `turn_end` (the start or end of the bearer's own turn), `helped`, `harmed` or `saving_throw` (the bearer makes one). `every` (the four timed moments only) makes it fire on every *n*-th one. `action` is `damage` or `heal` (`amount` plus `percent` of the maximum of `resource`, life if left out; damage of a given `attribute`), `skip_turn` (only at `turn_start`; the turn is lost on a roll up to `chance` out of 100) or `end` (the token goes). Ticks that `damage` or `heal` don't start further `helped` or `harmed` ticks.
   - A **modifier** is `{"kind": "damage_taken" | "damage_dealt", "attribute", "factor"}` (damage of that attribute, or `all`, is multiplied by `factor`) or `{"kind": "stat", "stat", "amount"}` (the stat's current value is raised or lowered while the token lasts).
   - `duration` is in rounds (none: until something removes it) and `intensity` is `{"shape": "flat" | "rising" | "falling", "high", "low"}`, how strong the token is over its life: `flat` is always `high`; `falling` starts at `high` and reaches `low` on the last round; `rising` the other way. Everything a token does is scaled by its intensity (a modifier's factor eases towards 1). A status with no duration is flat.
   - A status placed again by the *same* source refreshes that source's token; a different source's token is its own.
2. **Direct stat adjustment.** The effects `increase_stats`, `decrease_stats` and `steal_stats` move the *current* value of a non-resource stat (an `AlterStat` event), within `Rules.stat_range` (by default 0 to twice the base), and at the end of every round it drifts back towards its base (`Rules.stat_drift`: a quarter of the gap, at least 1).

**Who earns from a status.** A token's source is the actor behind everything the token does. Damage and healing from its ticks go through the usual path with the source as the actor, so `Rules.gauge_moved` sees the source earning it, at the intensity the token had. A tick that moves no gauge (a lost turn, say) is reported to `Rules.status_acted(fight, source, target, status, intensity, ratio)`, where `ratio` is the status's `xp_share` times the intensity: the share of the bearer's PXP (its potential experience, DragonStar's measure of what a character is worth) that the source is credited. The experience tree turns these into debts owed to the source.

Events: `StatusApplied`, `StatusTick`, `StatusRemoved` (with the reason: `expired`, `removed`, `ended` or `died`), `TurnSkipped` and `RoundEnd` (a round has gone by for the tokens; logged only when some fighter carries one). `fights.replay.apply_events` follows them, so tokens survive replays and spectators see them. A fight starts with the statuses it knows (`build_fight(layout, statuses)`; `fights.content.status_spec` makes one from a content row), and its snapshot carries them with every fighter's tokens.

## Fight rules

A fight is DragonStar's process: every fighter has chosen a command (attack with the left or right hand, use an item, change gear, use a skill, cast a spell, defend, run), then the round resolves in one pass and returns *events* (`Turn`, `Attack`, `Damage`, `Miss`, `Died`, ...) that say what happened. The fight's state after the round is what you get by applying its events in order (`terraforma.fights.replay.apply_events`), so a stored fight can be replayed and shown to spectators.

- **The rules** are the class `terraforma.fights.rules.Rules`, and `Game(rules=...)` hands the engine yours. `Rules()` is DragonStar's, unchanged: speed (the fighter's Speed spread by 15%, a caster slowed by the spell's cost against their Focus, multi-strike weapons acting several times, ties shuffled), the hit roll (always out of 100: it hits on a roll up to Accuracy ÷ (Accuracy + Dodge) as a percentage, so a critical, a roll of 1 that doubles Strength, is one hit in a hundred whatever the stats are), damage (Strength against Block, scaled by how cleanly the roll hit, from half at the edge of the hit chance to full at a 1, halved when defending, never under 1), the saving throw (Power against Resistance: none, half or all of a harmful spell gets through), and what each effect does. A game overrides any of them:

  ```python
  class MyRules(Rules):
      stats = (*Rules.stats, "Luck")

      def chance_to_hit(self, rng, accuracy, dodge):
          ...
  ```

  This is a public interface (the license exception covers it): the method names and signatures are what games build on.
- **Stats and resources.** `Rules.stats` is the list seeds are checked against. A *resource* is a stat that is a pool: its base is the maximum and its current value what's left. `Rules.resources` lists them (HP, which is life, and MP by default); a game adds its own (rage, technique points) and fills and drains them from the hooks `ability_cost`, `gauge_moved` (called whenever one fighter moves another's resource) and `after_event` (called for every event, and may add more).
- **Statuses and moving stats.** `Rules.stat_range(stat, base)` and `Rules.stat_drift(stat, current, base)` set how far a pushed stat can go and how fast it comes back; `Rules.slay_chance(rng, effect)` decides a slay; `Rules.status_acted(...)` is the hook for a token's source earning from it (see Statuses).
- **Allies, enemies and neutrals.** How parties stand to each other is `Rules.alignment(fight, party)`, returning that party's (allies, enemies); a party in neither is neutral to it. A party is always its own ally. Each `Party` may carry its own `allies` and `enemies` sets; with neither set, as in DragonStar, each party is for itself and every other party is an enemy. The seed's `all_allies`, `all_enemies`, `all_not_allies` and `all_not_enemies` targets follow it, and a game can override `alignment` for alliances and factions.
- **Dice.** Never the `random` module: every roll comes from a `WorldRng` stream, one per fight under its map (`fight_stream(world_rng, map_name, fight_id)`), drawn in a fixed order, so the same fight replays exactly.
- **Storing a fight** (`terraforma.fights.store`): a fight is its *initial state*, written once when it starts (`fights.state.dehydrate` turns a `Fight` into plain JSON and `hydrate` back), plus an append-only log of rounds. Each round records the commands that led to it, the events it produced, and a hash of the round before (the first chains from a hash of the initial state), so a changed or missing round shows. `load_state` rebuilds the fight now by replaying the log; `verify` checks the chain, and with `deep=True` plays every round again from its commands and its own dice (`fight_stream(world_rng, map, fight_id, round)`) and expects the same events. Anyone not given a command in a round defends. Heroes and monsters become fighters with `fights.build.hero_fighter` and `monster_fighter` (a hero starts a fight at full HP and MP for now).
- **Pure.** `terraforma.fights` (apart from `store` and `build`) has no database or web code. Heroes and monsters become fighters through `terraforma.fights.content`.

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

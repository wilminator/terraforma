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
docker compose up app                       # the engine alone, http://localhost:8000 (Postgres)
docker compose run --rm test                # every test, on SQLite, Postgres and MySQL
```

Settings come from `settings.toml`, not environment variables. The NAS the game runs on may not pass environment variables or Docker secrets into the container. `python -m terraforma check-settings` says what's wrong with the file, if anything.

On start, the container checks the settings, brings the database up to date (`python -m terraforma serve`), then serves. `GET /api/health` answers the container's health check.

## Changing the tables

Change the models, then make a migration and read it over, since autogenerate misses renames:

```sh
alembic revision --autogenerate -m "what changed"
```

A test fails whenever the models and the migrations disagree.

## Seed data

A game's starting content is JSON in its `seed/` folder: one file per kind of content, each a list of objects. `terraforma.seed.load_seed` reads and checks it.

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

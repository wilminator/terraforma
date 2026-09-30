# TerraFroma

TerraFroma is an RPG engine, and **Vanguard Tavern** is the first game built on it. It's the Python successor to DragonStar.

- `src/terraforma`: the engine (accounts, fights, AI, the server calls, the admin, the world).
- `src/vanguard_tavern`: the game (seed data, assets, settings). The game plugs into the engine; the engine never imports the game.

## Running it

Everything runs in Docker.

```sh
cp settings.example.toml settings.toml      # then set session_secret
docker compose up app                       # http://localhost:8000 (Postgres)
docker compose run --rm test                # every test, on SQLite, Postgres and MySQL
```

Settings come from `settings.toml`, not environment variables. The NAS the game runs on may not pass environment variables or Docker secrets into the container.

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

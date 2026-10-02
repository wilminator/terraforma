# Working on TerraForma

- The engine's name is TerraForma (the TerraForma RPG Engine). "TerraFroma" is a common typo for it: always correct it.
- Mike runs the tests for validation. Write tests with every change, run them where you can, and say which ones you ran. The full suite runs with `docker compose run --rm test`.
- This is the public engine (AGPL-3.0 plus the module permission in LICENSE-EXCEPTION.md). Never add game content, secrets or anything from the private Vanguard Tavern repo here. The engine runs in Docker; games deploy it (Vanguard Tavern on a QNAP NAS). Settings come from `settings.toml`, never from environment variables. The only exception is `TERRAFORMA_TEST_DATABASES` for the test runner.
- The target is Python 3.14.
- No warnings: keep builds, tests and CI free of deprecation and other warnings. Mike treats visual noise as something that hides real problems, so fix a warning rather than tolerate it.
- The engine never imports a game. Games plug in through the public interfaces (Game, create_app, seed formats, terraforma.testing): changing those is a change to what the license exception covers, so keep them deliberate and documented.
- Database-agnostic: follow the rules in README.md. Database-specific code goes only in `terraforma/db/dialect.py`. Every database test must pass on SQLite, Postgres and MySQL.
- Map-ready: new things that exist somewhere get a location. Randomness comes from `WorldRng` streams, never the `random` module. Time comes from the world clock.
- Every server call is its own route, with a strict Pydantic model for its arguments. Calls that change something depend on `ActingAccount` (login plus CSRF token); calls that only read use `CurrentAccount`. The fight WebSocket checks the Origin header.
- Security rules carried from DragonStar:
  - Passwords use Argon2id.
  - An email change needs an OTP (when 2FA is on) or an email link, never the password.
  - Changes to 2FA are confirmed by a link.
  - Logs are scrubbed.
  - Logins and sensitive calls are rate-limited.
  - Encryption keys have two slots.
  - A player's public handle can't match their username or email.
  - Admins earn tokens only when fighting as a player.
- Any asset in this repo (art, sound, music, fonts) must be legally usable and compatible with a public repo: Mike's own, or openly licensed (CC0, CC-BY with credit). Record each asset's source and license beside it, and never add one whose license is unknown. Nothing from DragonStar's assets.
- The browser code ported from DragonStar gets no feature work until DragonStar's features are ported. Fixes needed for the new server are fine.
- The plan lives in the TerraForma plan doc. Work goes on branches with pull requests; tag releases (v0.x.y) for games to depend on.

# Work plan while Mike's cloud-session credits last

Mike has a pool of cloud-session credits and wants them spent carefully. This section is the plan for splitting the work into small sessions, and it is kept up to date until Mike says the credits are exhausted (then delete this section). Whoever finishes a slice updates its status here, in that slice's own pull request, not in a separate one.

How to work under it:
- One slice per session, one pull request per slice. A fresh session reads this file, so the slice's brief below is all it needs. Don't carry a long conversation from slice to slice: it costs more with every message.
- When running on credits, confirm with Mike how deep to go before starting a slice (for example: the whole slice, or only its first piece). Write the tests; Mike runs them for validation. Say which tests you ran and where.
- Don't poll CI. Mike reports results.
- Slices that touch the same files (accounts routes and service, the migration chain) run one after another, never side by side: migration numbers and routes would collide. Only independent slices run in parallel.
- Don't spend credits on Ultrareview for routine pull requests. Keep it for a release.

## Slices

Status is one of: done, next, waiting (names what it waits for), idea (not yet scoped; confirm with Mike first).

1. **Keys in two slots, and the public handle**: done (engine #3, game #10).
2. **2FA (TOTP)**: done (engine #5). Setup, login with a code, recovery codes, and turning it off.
   - The secret is stored encrypted: register its column with `keys.encrypted_column`.
   - Any change to 2FA (turn on, turn off, new recovery codes) is confirmed by an emailed link, which is a new token purpose in `accounts/tokens.py`.
   - A code can't be used twice; the 2FA calls and the login code check are rate-limited (`accounts/ratelimit.py`, in the database).
   - New migration 0004. Every table and call works on SQLite, Postgres and MySQL.
3. **Email change**: done (engine #6). With 2FA on, a live code is needed as well as the link mailed to the new address. Without 2FA, an emailed link to the new address does it. Never the password. Re-check the handle against the new address (`check_handle`), end other logins (`session_version`), and tell the old address.
4. **Release v0.2.0**: done (tagged by Mike). The games' dependency changes from `@main` to the tag only if Mike says the API is stable enough; until then they follow `main`.
5. **Content and fights**: proposed, waiting for Mike to confirm the order and depth of each piece (the pieces below replace the old "other work" idea; they follow the plan doc's phases as the code's comments name them). They share the models module and the migration chain, so they run in order, one pull request each. Each takes the next free migration number when its branch is cut.
   - **5a. Content models and seed loading** (done: engine PR from branch `content-models`, migration 0005): models for abilities, items, jobs, monsters and personalities; strict Pydantic checks of each seed file (`seed.py`); an idempotent load into the database at startup (`db.dialect.upsert`), so a game's seed is data the engine understands. The seed file formats are a public interface (the license exception covers them): document them in the README. Unblocks the game module loading real content.
   - **5b. Heroes and teams** (done: engine PR from branch `heroes-teams`, migration 0006, stacked on 5a): a hero belongs to an account (a job, stats, a location), a team groups heroes; calls to create, list and rename them, with limits per account.
   - **5b2. Inventory, equipment and abilities for heroes** (next; migration 0007): heroes get what DragonStar's heroes have, adapted to this framework (read `include/hero.php`, `character.php`, `item.php` in the private repo for the concepts; write fresh code): an inventory of items with counts, equipment slots (the slot names come from items' `equip_slots`), the abilities a hero knows (from the job), and gold. Calls to equip, unequip and list; limits and rules as DragonStar has them. Needed before fights can use a hero's weapon and spells.
   - **5c. Fight engine core and the rules framework** (migration 0008): carries DragonStar's rules over as the engine's defaults, "the process mirrored, functionally equivalent" (the format may differ):
     - Rounds: every combatant commits a command (attack left or right hand, item, equip, skill, spell, defend, run), then the round resolves in one pass. Order is by Speed randomised +-15%, spell casting slowed by Focus against MP cost, multi-attack weapons appear several times, ties shuffled. Hit roll `1..Accuracy+Dodge` (a roll of 1 is a critical that doubles Strength); damage from Strength against Block scaled by roll quality, halved when defending, minimum 1; harmful skills can be dodged; harmful spells get a Power against Resistance saving throw (none, half, full). Same rules, same numbers.
     - Randomness: a named `WorldRng` stream per map (`stream("map", name)`), and each fight draws from its own stream under it (`stream("map", name, "fight", id)`), so any fight replays exactly. Never the `random` module.
     - The fight record is an append-only list of typed events per action with a sequence number (DragonStar's `FightAction`); state is the fold of those events, so spectators and replays work. Each action row also stores a hash of the previous row (a plain hash chain: tamper-evident and cheap, not a blockchain, because the server is the only authority and there is nothing to agree on).
     - Pure functions for order, hit, damage, saving throw and effects, no database or web code in them; models for fight, participant and action; party, group and character addressing as `FighterRef` already has.
     - **The framework a game overrides** (the public interface; document it): a `Rules` base class holding the formulas (speed, chance to hit, damage, saving throw, effects), the stat list, and the *resource* list. Resources are the pools like HP and MP; a game can define others (rage, technique points) with their own maximum, gain and spend rules; HP-style death at zero stays the default. Seed checking takes its stat names from the game's rules instead of a fixed list. Advancement is a hook as well (`on_fight_end` and friends), with level-based advancement as the default implementation, so a game can later grant stat bonuses for performance or sell abilities for an experience currency. Build only what is there now (the DragonStar behaviour), shaped so those additions don't need a rewrite; if one is cheap, include it.
   - **5c2. Statuses** (needs 5c): a content kind (`statuses.json`, a public seed format) and a token on a combatant. Two mechanisms, both supported: (1) *status tokens* that process at their own times (start or end of round, start or end of every turn or the nth turn, when helped, when harmed, on a saving throw, ...) and can do damage, skip turns, modify damage by element and so on, with an optional duration and an intensity that is a function of the time left (rising, falling or flat); (2) *direct stat adjustment*, where the current value of a non-resource stat moves and drifts back to its base over time. Event types for each, so the log still replays.
   - **5c3. Experience and rewards** (needs 5c; can run beside 5c4): DragonStar's experience tree (who earned what from whom, the debts and credits, distribution when a party leaves or wins), gold and drops at a fight's end, written to the event log, with advancement going through the rules framework's hook.
   - **5c4. Monster AI** (needs 5c; can run beside 5c3): how monsters (and NPCs) choose a command each round, from the `ai` fields the monster seed has (action, goal, target, experience) and DragonStar's AI process; draws only from the fight's stream so fights still replay.
   - **5d. Fights live**: the command call stops being a stub and feeds the engine; the WebSocket pushes real events; the round timer runs on the clock. Admins earn tokens only when fighting as a player.
   - **5e. Maps and movement** (not before the map generator and editors exist, so parked): map tiles, a move call (wrapping maps included), heroes and monsters placed on maps. Independent of 5c and 5d except for the migration number, so it can run beside them.
   The game module can start on its seed files, assets and rules settings right after the v0.2.0 release; loading them works since 5a, playing them waits for 5c to 5d. 5c3 and 5c4 are the pieces that can run side by side; each takes the next free migration number when its branch is cut and rebases before its pull request.

## Parallel runs

Slices 2 and 3 must be in order. Slice 5's pieces can run beside slices 2 to 4 in their own sessions, as long as they don't change accounts files; each takes the next free migration number when its branch is cut and rebases before the pull request.

## Log

One line per finished slice or change of plan: date, what, pull request.

- 2026-09-30: slice 1 merged (engine #3, game #10). Games follow the engine's `main` (game #8, example #3); the game promotes `staging` to `main` itself (game #12).
- 2026-10-01: slice 2 (2FA) merged (engine #5); slice 3 (email change) in review (engine #6). Test run sped up: databases in RAM, `pytest -n auto` with a database per worker (engine #7).
- 2026-10-01: slice 3 merged (engine #6), CI sped up (engine #9), slice 5 scoped (engine #8). v0.2.0 tagged: slices 1 to 3 are in it. Next: confirm slice 5's order and depth with Mike, then 5a.
- 2026-10-02: 5a (content models and seed loading, engine #11) and 5b (heroes and teams) built; 5e parked until the map generator and editors exist. Tests run on a fixed clock (engine #12). Next: 5c, the fight engine core.
- 2026-10-02: 5a (engine #11) and 5b (engine #13) merged. 5c scoped with Mike: DragonStar's rules carried over as the engine defaults behind an overridable `Rules` class (formulas, extensible stats and resources, advancement hook); inventory and equipment first (5b2), then the fight core (5c), then statuses (5c2), then experience (5c3) and monster AI (5c4) side by side, then live fights (5d).

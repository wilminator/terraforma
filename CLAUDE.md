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
  - Admins earn Challenge Tokens only when fighting as a player.
  - The one call with no login is the server-to-server Challenge Token purchase (`/api/server/challenge/purchase`): off unless `settings.toml` sets a secret, which it must send as a bearer token; it takes an idempotency key.
- Any asset in this repo (art, sound, music, fonts) must be legally usable in a public AGPL repo: Mike's own, or under a license compatible with the AGPL (CC0, CC-BY, CC-BY-SA 4.0, MIT/BSD, OFL, GPL/AGPL; never non-commercial, no-derivatives or CC-BY-SA 3.0 and older). Record each asset's source and license beside it (see `src/terraforma/example/assets/ASSETS.md`), and never add one whose license is unknown. Nothing from DragonStar's assets.
- The browser client is plain ES modules and CSS with no build step (`src/terraforma/client`), written fresh, not ported from DragonStar. Pages hold no inline script (the Content-Security-Policy refuses it) and build DOM with `h()`, never `innerHTML`. A game adds browser code through `Game(client_dir, client_modules, client_styles)` and the shell's hooks (README, "The browser client"): changing those is a change to what the license exception covers. Browser tests (`tests/e2e`, Playwright, the example game) run in CI on SQLite for every pull request; the label `browser-all-databases` adds Postgres and MySQL.
- Work goes on branches with pull requests, one slice per pull request; Mike creates the release tags (v0.x.y) that games depend on.
- Tests that depend on dice must not depend on luck: a world's seed is random per test, so fix it (`World.seed`) or script the rolls.
- Anything stored whose exact text matters (the fight log's hash chain) uses `ExactJSON`, not `JSON`: MySQL re-formats floats in JSON columns.
- Migrations are linked only by `down_revision`. Take the next free number when your branch is cut; when another branch merges first, re-point yours at the new head and rename it.

# Planning and how to work

The work plan lives in the shared Claude doc "TerraForma work plan" (https://claude.ai/code/artifact/c816de4c-85ee-417e-8818-0e2364b783bd), not in this file, so this file stops changing with every slice. The doc holds the slices and their status, open questions for Mike, decisions made (with dates) and the log. Code and the rules the code follows live in this repository (README, this file).

- Read the doc before starting a slice, and don't start a piece another thread has in flight.
- When you start, finish or change a slice, edit the doc directly (no pull request): update its row, add one line to the log (newest first), and record any decision Mike makes under Decisions. Put questions for Mike under Open questions and remove them once he answers.
- Mike pays for cloud sessions out of a pool of credits. Confirm with him how deep to go before starting a slice, keep a session to one slice, and don't poll CI (he reports results). Don't spend credits on Ultrareview for routine pull requests; keep it for a release.
- Slices that touch the same files (accounts routes and service, the models module, the migration chain) run one after another. Run at most two threads at a time.

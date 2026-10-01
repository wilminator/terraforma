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
2. **2FA (TOTP)**: next. Setup, login with a code, recovery codes, and turning it off.
   - The secret is stored encrypted: register its column with `keys.encrypted_column`.
   - Any change to 2FA (turn on, turn off, new recovery codes) is confirmed by an emailed link, which is a new token purpose in `accounts/tokens.py`.
   - A code can't be used twice; the 2FA calls and the login code check are rate-limited (`accounts/ratelimit.py`, in the database).
   - New migration 0004. Every table and call works on SQLite, Postgres and MySQL.
3. **Email change**: waiting (on slice 2, since it needs the 2FA code check). With 2FA on, only an OTP changes the email. Without it, an emailed link does. Never the password. Re-check the handle against the new address (`check_handle`), end other logins (`session_version`), and tell the old address.
4. **Release v0.2.0**: waiting (on slices 2 and 3). Mike creates the tag (Claude's sessions can't create tags). Then the games' dependency changes from `@main` to the tag only if Mike says the API is stable enough; until then they follow `main`.
5. **Other work from the TerraForma plan doc** (fights engine, maps, seed loading and the rest): idea. Independent of accounts, so it can run alongside slices 2 to 4. Scope it with Mike first.

## Parallel runs

Slices 2 and 3 must be in order. Slice 5 work can run beside them in its own session, as long as it doesn't change accounts files or add a migration in the same release window; if it needs a migration, take the next free number when the branch is cut and rebase before the pull request.

## Log

One line per finished slice or change of plan: date, what, pull request.

- 2026-09-30: slice 1 merged (engine #3, game #10). Games follow the engine's `main` (game #8, example #3); the game promotes `staging` to `main` itself (game #12).

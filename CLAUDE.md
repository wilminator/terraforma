# Working on TerraForma

- The engine's name is TerraForma (the TerraForma RPG Engine). "TerraFroma" is a common typo for it: always correct it.

- Mike runs the tests for validation. Write tests with every change, run them where you can, and say which ones you ran. The full suite runs with `docker compose run --rm test`.
- The app is built to run in Docker (on a QNAP NAS in production). Settings come from `settings.toml`, never from environment variables. The only exception is `TERRAFORMA_TEST_DATABASES` for the test runner.
- The target is Python 3.14.
- The engine (`terraforma`) never imports the game (`vanguard_tavern`). Game content is data (JSON seed files) and assets.
- Database-agnostic: follow the rules in README.md. Database-specific code goes only in `terraforma/db/dialect.py`. Every database test must pass on SQLite, Postgres and MySQL.
- Map-ready: new things that exist somewhere get a location. Randomness comes from `WorldRng` streams, never the `random` module. Time comes from the world clock.
- Every server call is its own route, with a strict Pydantic model for its arguments. Calls that change something depend on `acting_account_id` (CSRF). The fight WebSocket checks the Origin header.
- Security rules carried from DragonStar:
  - Passwords use Argon2id.
  - An email change needs an OTP (when 2FA is on) or an email link, never the password.
  - Changes to 2FA are confirmed by a link.
  - Logs are scrubbed.
  - Logins and sensitive calls are rate-limited.
  - Encryption keys have two slots.
  - A player's public handle can't match their username or email.
  - Admins earn tokens only when fighting as a player.
- Assets (art, sound, music, fonts) must be legally usable. Many of DragonStar's assets are copyrighted and can't come across: they need replacements. Use only assets Mike made, commissioned, or that carry a license allowing use in the game (for example CC0, or CC-BY with credit given). Record each asset's source and license beside it, and never add one whose license is unknown.
- The browser code ported from DragonStar gets no feature work until DragonStar's features are ported. Fixes needed for the new server are fine.
- The plan lives in the TerraForma plan doc. Work goes on branches with pull requests.

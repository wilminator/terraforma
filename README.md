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

Migrations are numbered `0001`, `0002`, ... in the file name and the revision id, and each points (`down_revision`) at the one with the number before. Take the next free number when you cut your branch. When another branch merges first, re-point yours at the new head and rename it: a test fails on a repeated or missing number, a second head, or a revision that doesn't follow the one before it.

## Seed data

A game's starting content is JSON in its `seed/` folder (`Game(seed_dir=...)`): one file per kind of content, each a list of objects. These formats are a public interface, so changing one is deliberate and noted here.

| File | A row has |
|---|---|
| `abilities.json` | `key`, `name`, `kind` (`spell` or `skill`), `mp_cost`, `description`, `icon`, `effect`, `presentation` |
| `items.json` | `key`, `name`, `price`, `one_use`, `description`, `icon`, `use_effect`, `equip_slots`, `stat_bonus`, `stat_percent`, `attack`, `use_presentation`, `fight_presentation` |
| `jobs.json` | `key`, `name`, `xp_needed`, `stat_growth` (stats per level), `abilities` (each `{"ability": key, "level": n}`, the level a hero gets it at; a bare key means level 1) |
| `personalities.json` | `key`, `name`, an animation for each of `base equip flee hit die attack_close attack_throw attack_shoot skill spell item`, and `overworld` (`stand` and `move`, each facing `up down left right`) |
| `monsters.json` | `key`, `name`, `personality` (key), `xp_reward`, `gold_reward`, `stats`, `abilities`, `items`, `equipment` (keys), `drops` (drop table keys), `ai` |
| `drop_tables.json` | `key`, `name`, `weighted`, `rolls`, `entries` (see Item drops below) |
| `statuses.json` | `key`, `name`, `kind` (`good` or `bad`), `description`, `icon`, `duration`, `intensity`, `ticks`, `modifiers`, `xp_share` (see Statuses below) |

- **Keys.** Every row has a `key` (1-64 lowercase letters, digits, `_` or `-`) that the game picks and never reuses. Rows name each other by key. Only `key` and `name` are required; the rest have defaults.
- **Equipment slots.** An item's `equip_slots` lists the slots it takes. The engine's slots are `rhand rammo rarm lhand lammo larm body head back feet`; `hand`, `ammo` and `arm` are *sided* (the player picks left or right when equipping), and a two-handed weapon lists `lhand` and `rhand`. A weapon's `attack.ammo_type` has to match its ammunition's. Gear never stacks; ammunition and non-equipment stack to 250, in at most 12 stacks (for now these limits are constants in `heroes/inventory.py`).
- **Stats** are the game's `Rules.stats` (see Fight rules below), by default `HP MP Speed Accuracy Strength Dodge Block Power Resistance Focus`; a stat left out is 0, and any other name is refused.
- **An effect** is `{"effect", "targets", "base", "added", "attribute", "stats", "status", "duration"}`: `effect` is one of `none heal hurt revive slay increase_stats decrease_stats steal_stats cause_good_status remove_good_status cause_bad_status remove_bad_status restore_mp`, `targets` is `individual`, `group`, `party`, `all_parties` (every party, the actor's own included), `all_allies`, `all_enemies`, `all_not_allies` (enemies and neutrals), `all_not_enemies` (allies and neutrals), `random_party` (one whole party, drawn from the fight's own random stream so the fight replays, from the parties `Rules.random_party_pool` names: by default every party with someone alive, the actor's own included; a game narrows it), or a number *n* (the target and *n* neighbours each way along its group, hit less the further they are), and `attribute` is the game's own kind of damage. `slay`'s `base` is its chance out of 100. The stat effects (`increase_stats`, `decrease_stats`, `steal_stats`) need `stats`, the non-resource stats they push (`base` and `added` are the amount); `cause_good_status` and `cause_bad_status` need a `status` (a key in `statuses.json` of the matching kind) and may set `duration` (rounds) over the status's own; `remove_good_status` and `remove_bad_status` take off the one `status` names, or every status of their kind if it is left out.
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

**Who earns from a status.** A token's source is the actor behind everything the token does. Damage and healing from its ticks go through the usual path with the source as the actor, so `Rules.gauge_moved` sees the source earning it, at the intensity the token had. A tick that moves no gauge (a lost turn, say) is reported to `Rules.status_acted(fight, source, target, status, intensity, ratio)`, where `ratio` is the status's `xp_share` times the intensity: the share of the bearer's PXP (its potential experience, DragonStar's measure of what a character is worth) that the source is credited. By default the bearer owes the source that share of its PXP as an `XpDebt` (harm for a bad status, help for a good one), paid out with the rest of the experience when the fight ends.

Events: `StatusApplied`, `StatusTick`, `StatusRemoved` (with the reason: `expired`, `removed`, `ended` or `died`), `TurnSkipped` and `RoundEnd` (a round has gone by for the tokens; logged only when some fighter carries one). `fights.replay.apply_events` follows them, so tokens survive replays and spectators see them. A fight starts with the statuses it knows (`build_fight(layout, statuses)`; `fights.content.status_spec` makes one from a content row), and its snapshot carries them with every fighter's tokens.

## Parties

A party is a collection of *whole teams* (`terraforma.parties.service`). A team joins whole and leaves whole, never split; a team is in at most one party; the smallest party is one team; and parties merge (all of the second party's teams join the first, in their order, only if they fit together). Teams from different accounts can share a party. A party stands on a map like everything else (where its first team's first hero stands; the hub for an empty team).

How many heroes a party holds is the game's rule: `Rules.party_size`, 20 by default, and `Rules.group_size`, 5 by default, the size of the groups a fight's side is laid out in (four groups of five). A team joins only if there is a place for every one of its heroes, and a hero added to a team that is in a party needs a place too (the add-hero call follows the game's `party_size`). Deleting a team takes it out of its party, and an empty party goes with its last team.

These are service functions, not server calls: the map drives them later (an interaction on the map forms and merges parties), so they take ids and whoever calls them decides who may. A party becomes one side of a fight with `fights.build.party_side(session, party_id, rules)`: its heroes team by team, in the order the teams joined, in groups of `group_size` (`build_fight({0: await party_side(...), 1: ...})`).

## Fights between parties (PvP)

Where a party may pick a fight with another party is a rule, in two parts. The place: `Game(pvp=...)` (`terraforma.pvp.hooks.PvpZones`) says whether a spot allows PvP, `allows_pvp(session, map_id, x, y)`; the engine's default is nowhere. The range window: `Rules.may_start_pvp(attacker_pxp, target_pxp, allowed)` returns None if the fight may start, otherwise why not. By default it refuses where PvP is off, and where it is on it allows a target whose party PXP is at least `Rules.pvp_window` (0.85) of the attacker's, and always a stronger one. A party's PXP is the sum of its fighters' (`terraforma.pvp.service.party_pxp`), and `terraforma.pvp.service.refusal` runs both checks. These are public interfaces (the license exception covers them).

To pick a fight, a team's owner calls `POST /api/pvp/fights` with `team_id` (their own team; its party picks the fight) and `party_id` (the party to fight), with login and CSRF token. The fight starts with the caller's party as party 0 and the other as party 1, and each player commands their own heroes. It is refused with a 409 and the reason unless both parties stand at the same spot (same map and tile), neither is in a town or in a fight, the place allows PvP and the other party is inside the window. A team not in a party, or not the caller's, is refused too (404 for a team that is not theirs). There is no way yet to list the parties at a spot; the map will offer that. What a PvP fight pays out is the engine's ordinary result (experience, no gold or drops unless the game's Rules say so).

Vanguard Tavern's setup: towns and instance dungeons are PvE only, everywhere else is PvP, with the default window (a game that wants 80% sets `pvp_window = 0.8` on its Rules).

        class Zones(PvpZones):
            async def allows_pvp(self, session, map_id, x, y):
                return not (await TOWNS.is_town(session, map_id, x, y) or map_id in INSTANCE_MAPS)

## Towns

In a *town* a party comes apart. The game says which places are towns (`Game(towns=...)`, `terraforma.towns.hooks.Towns`: `is_town(session, map_id, x, y)`, nowhere by default; the map will ask it as a party moves), and `terraforma.towns.service.enter_town` suspends a party there: it is split into groups of its teams (`Towns.groups`: by default one team each; a game can keep a player's own teams together, for example because the player paid for it), and each team shops and rests on its own. The party can't be joined, merged or have a team leave while it is apart (`parties.service` refuses).

To leave, a team (with the teams in its group) says it is ready (`ready`; `Towns.may_wait` can refuse, for a team in a fight) and waits in a holding queue. Every other team of the party is *told* (a notice: `view` lists who is ready) and can follow, which is the same call. A waiting team can `come_back` into the town, or `leave_party` for good (it is not waited for any more). When every team left is waiting, the party is put back as one unit, in the formation it had (its teams' order), and every hero of it is placed at the party's spot on the map (`reform`). Entering a town and putting a party back are service functions the map drives; a team's own owner calls `GET /api/teams/{id}/town` and `POST .../town/ready`, `.../town/come-back` and `.../town/leave-party` (login and CSRF token; each its own route). These are public interfaces (the license exception covers them).

## Item drops

A drop table (`drop_tables.json`) says what a monster, or an area, leaves behind. A monster names the tables it rolls when it dies (`drops`); a fight may carry *area* tables of its own (`live.start_team_fight(..., area_drops=[keys])`; the map's, once there are maps).

- **A plain table** rolls every entry on its own: an entry is `{"item", "chance", "min", "max", "for"}`. `chance` is out of `Rules.drop_chance_scale` (10000 by default, so 10000 is a sure thing and 1 is one in ten thousand); `min` and `max` (default 1) say how many. **A weighted table** (`"weighted": true`) instead picks an entry by `weight`, `rolls` times; an entry with no `item` means nothing drops.
- **Who gets it.** An entry's `for` is `one` (one person, the default), `each_team` (one in each team of the winning side) or `each_member` (every hero of the winning party). Who that person is, is the game's rule: `Rules.drop_recipients(fight, party, monsters, share, rng)`. The default picks one at random among the heroes who *contributed*, which it reads off the experience debts the fight keeps: whoever harmed a monster that died (a hit, or through a status, or by placing a bad status on it) and whoever placed a good status on one of those. Healing does not count. Everyone in the pool has the same chance, and a hit for 1 HP counts the same as any other. A game overrides it for a leader's choice, the last strike, round robin, a team chest and so on (choices that need players to answer later are *held*: see **Held drops** below).
- **When.** `Rules.roll_drops` runs when a fight ends, after the experience and the gold, for each party of players that won: the tables of every monster that died, and the area's tables once for the fight (`Rules.map_drops_once_per_fight`, on by default; off rolls them for every monster instead). The dice are the fight's own stream, so a fight replays exactly.
- **What lands.** A `Drop` event, and the item goes into the hero's inventory in the fight; what does not fit (twelve stacks, 250 to a stack, gear never stacking: `Rules.inventory_stacks` and `stack_size`) is a `DropLost` event, never lost silently. `store.apply_results` puts the dropped items in the heroes' inventories, once.
- **Held drops.** `Rules.drop_mode(fight, party, monsters, share, rng)` says how one drop is given out: `"auto"` (the default, `drop_recipients` at once), `"need_want"` or `"assign"`. A held drop is a `DropHeld` event `[party, item, quantity, mode]`, nobody is given it, and `store.apply_results` makes a row of it in `pending_drops` (once, however often it is saved) standing where the fight did. A *need/want* drop waits until every hero of the party that fought has said `need`, `want` or `pass` (changing their mind is fine until the last one answers); then those who needed (or, if none did, wanted) roll 1 to 100 from a stream of the fight's own (`"drop", n`), the highest wins and a tie goes to the lower hero id. If all pass it is `unclaimed`. An *assign* drop is given to a hero of the party by whoever `Rules.may_assign_drop(hero_ids, hero_id)` allows: the engine has no party leader, so the default is nobody and a game that has one overrides it. What does not fit the winner's pack is counted in `lost`, not lost silently. Calls (each its own route, login and CSRF token on the writes): `GET /api/heroes/{id}/pending-drops`, `POST .../{drop_id}/choose` (`{"choice"}`) and `POST .../{drop_id}/assign` (`{"to_hero_id"}`). Others' answers stay hidden until the drop is settled.
- **Checks.** The seed refuses a table that names an unknown item, a chance outside 1 to the scale, a minimum above its maximum, an empty table, a plain entry with no item or a weight, a weighted one with no weight or a chance, and a monster that names an unknown table.

## Gold and the economy

Where gold lives is the game's decision, so the engine asks `Game(economy=...)` (`terraforma.economy.Economy`). `TeamGold`, the default, is DragonStar's way: a hero on a team spends and earns from the team's gold (`Team.gold`), and a hero on no team has gold of their own (`Hero.gold`). `HeroGold` keeps every hero's gold on the hero and splits what a team earns between its heroes, the first by slot getting the odd coins. A game subclasses `Economy` for anything else, overriding `purse(session, hero)` (the row whose `gold` is the hero's money) and `credit_team(session, team_id, amount)` (what a team earns).

`balance`, `credit` and `debit` work on the hero's purse; a debit that would take more than is there raises `NotEnoughGold` and takes nothing. Gold moves with single statements, so two calls at the same instant never lose a coin. The inventory call shows the hero's purse in the game's economy. A fight's gold is paid by `economy.credit_fight_gold(session, economy, fight_record, [(team_id, amount), ...])`: once per fight (the fight records that it has paid), however often it is called. `fights.store.apply_results` does it from the fight's `Gold` events, through the game's economy. These are public interfaces (the license exception covers them).

## Trading

Heroes give each other gold and items, if the game's economy allows it. Who may trade with whom is `Economy.can_trade(session, giver, receiver, what)`, which asks the economy's `trade_policy` (`terraforma.trading.policy`). The stock policies are `NoOne`, `Anyone`, `WithinTeam` (the default: heroes on the same team), `WithinParty` (the same team, or teams in the same party), `Related` (whatever the economy's `related(session, giver, receiver)` says, the hook a game fills in for its own relationships; nobody until it does) and `AnyOf(...)` to combine them. A game sets one on its economy (`trade_policy = AnyOf(WithinTeam(), Related())`) or subclasses `TradePolicy` and writes `allows(economy, session, giver, receiver)`. These are public interfaces (the license exception covers them).

The calls, each its own route, for the hero's owner and with the CSRF token: `POST /api/heroes/{id}/give-gold` (`to_hero_id`, `amount`) and `POST /api/heroes/{id}/give-item` (`to_hero_id`, `position`, `qty`), and `GET /api/heroes/{id}/trades` for the hero's ledger. They are limited to 120 gifts an hour per account. An item moves only if it is not worn and all of it fits in the other pack; gold moves between the two heroes' purses in the game's economy, so heroes who share a purse (teammates under `TeamGold`) have nothing to give each other. Whether the other hero does not exist or may not be traded with, the answer is the same (404, "you can't trade with that hero"), so the calls cannot be used to find heroes. Every gift is a line in the ledger (`TradeRecord`), which keeps the heroes' ids and names so the history outlives a deleted hero.

## Relationships

A team has a relationship with another team: how it feels about it, as a score from -100 to 100 and a short private note. It is *directed* (Aria's view of Bram and Bram's view of Aria are separate rows) and private: only the team's owner reads it, and the other side never sees a score or a note. Scores have named *bands* (`enemy`, `wary`, `neutral`, `friendly`, `close` by default). Alliances are the same kind of side (see Alliances).

The game has the say, through `Game(relations=...)` (`terraforma.relations.hooks.Relations`). A fight can move them too, only if the game says so: when a fighter moves the gauge of one on another team whose party is not its enemy (an ally, a neutral or a partymate), `Rules.relation_moved(fight, actor, target, resource, before, after, maximum)` returns how much the actor's team's view of the target's team changes (the default is 0, so nothing moves). The rule may instead return `AskPlayer(delta, reason)` (from `terraforma.fights.rules`) to let the player decide: the log gets a `RelationPrompt` event (team, other team, suggested change, reason) for the owner of the actor's team to be asked about, and nothing moves until they answer (the engine's way to ask and record the answer is the after-fight rating prompt, below). A non-zero number is a `RelationChange` event (team, other team, change) in the fight's log, and `fights.store.apply_results` applies what the log holds, added up per pair, through `Game(relations=...)` once (the fight records that it has: a change the game's rules refuse, or for a team that has gone, is dropped). The engine never changes a score on its own; every change goes through `Relations.resolve(session, subject, object, current, change)`, which by default gives what was asked, within the scale. A game also sets `bands`, says where a new relationship starts (`initial`) and who may form one at all (`may_form`), and moves scores from its own rules by calling `terraforma.relations.service.apply(session, relations, Change(subject, object, delta=-5, by="game", reason=...))`. A `Change` says who asked (`by` is `"player"` for the owner in a call, `"game"` for the rules). These are public interfaces (the license exception covers them).

The calls, each its own route, for the team's owner (the ones that change something with the CSRF token, limited to 120 an hour per account): `GET /api/teams/{id}/relationships` (the bands, and this team's own views by name), `POST .../relationships/set` (`id` of the other team, and a `score`, a `note`, or both) and `POST .../relationships/forget` (`id`). A team's relationships go when it is deleted.

## Alliances

An alliance is a formal power structure of teams: a name, member teams in roles, and invitations. The game defines the roles and what each may do through `Game(alliances=...)` (`terraforma.alliances.hooks.Alliances`): `roles` (highest rank first; `leader`, `officer`, `member` by default), `founder_role` and `default_role`, `permissions` (which of `invite`, `withdraw`, `remove`, `set_role`, `hand_over`, `disband` and `speak` each role may do), `max_members` (counting invited teams) and `max_per_team`, and the hooks `may_found`, `may_join` and `allowed`, the one place to say otherwise (by default a team acts only on a team of a lower rank and gives only a role below its own). These are public interfaces (the license exception covers them). Voting and how an alliance is run beyond roles are the game's, on top of this.

A team's owner acts for it. A team founds an alliance and holds its founder role; a role that may invite invites a team, whose owner accepts or declines (it joins in the default role); a team leaves, or is removed; the only holder of the founder role can't leave while others remain and hands the role over first; the last team out, or a disband by a role that may, takes the alliance with it. A team that is deleted leaves its alliances, and an alliance left without a founder gives the role to its best-ranked, longest-standing team. Someone with no team in an alliance cannot tell that it exists (404).

The calls, each its own route (the ones that change something with the CSRF token, limited to 120 an hour per account): `GET /api/alliances` (your teams' alliances and the invitations waiting), `POST /api/alliances` (`team_id`, `name`), `GET /api/alliances/{id}`, `POST /api/alliances/{id}/invite|withdraw|remove|hand-over` (`team_id` of the acting team and `target_team_id`), `.../role` (also `role`), `.../leave` and `.../disband` (`team_id`), `GET /api/teams/{id}/invitations` and `POST /api/teams/{id}/invitations/accept|decline` (`alliance_id`).

**Ballots** put a question to the alliance. A role that may `open_ballot` gives a title (and `kind` and `payload` for the game's own use, which the engine keeps and never reads), two to ten options, whether it is `secret`, and when it closes; each team whose role may `vote` votes once, weighted as `Alliances.vote_weight` says (1 each by default). A ballot closes when its time is up, when everyone who could vote has (`close_when_all_voted`), or when a role that may `close_ballot` closes it; the game decides who wins (`Alliances.decide`, most weight by default, a tie decides nothing) and acts on it in `Alliances.on_ballot_closed`. Nothing waits on a timer: a ballot is closed, if its time is up, the next time anyone looks at it or votes, and `ballots.close_due` closes every due ballot for a game to call on its own schedule. A *public* ballot shows who voted for what, and a team may change its vote until it closes; a *secret* ballot keeps who voted apart from what was voted (the stored vote has no team on it), shows only the turnout until it closes and the totals after, and its votes are final. The calls: `GET /api/alliances/{id}/ballots` and `.../ballots/{ballot_id}` (members read; `?team_id=` picks which of your teams you read as), `POST .../ballots` (`team_id`, `title`, `options`, `kind`, `payload`, `secret`, `closes_in_hours`), `POST .../ballots/{ballot_id}/vote` (`team_id`, `option`) and `.../close` (`team_id`).

Alliances are sides in relationships too (see Relationships): a team can have a view of an alliance (`kind: "alliance"`), and an alliance has views of teams and of other alliances, read by its members and set by a role that may `speak` (`GET`/`POST /api/alliances/{id}/relationships`, `.../set`, `.../forget`).

**Rating after a fight.** When a fight between player teams ends, a team that was helped or harmed by another player team (read off the experience debts the fight kept: a hit, a heal, a status) and has no opinion of it is asked to rate it (`terraforma.relations.ratings`): a question kept until the player answers or dismisses it. Nothing changes by itself: an answer is an ordinary change to the relationship, so the game's `Relations.resolve` still decides the score that results, and `Relations.ask_after_fight(session, subject, object, interaction, score)` decides whom to ask at all (by default: only a team in the neutral band, `interaction` being `helped`, `harmed` or `both`). Monsters and a team's own side are never asked about. A game's own rule can ask too: a `RelationPrompt` event in the log (from `Rules.relation_moved` returning `AskPlayer`) always raises a question (the game said so), which carries the suggested change and the reason and can be accepted as it stands. The calls (each its own route, a login; the two that change something with the CSRF token, in the same 120 an hour): `GET /api/ratings` (the bands and the open questions, with the teams' names, the fight's public name and any suggestion), `POST /api/ratings/{id}/answer` (a `score`, -100 to 100, or `accept: true` for the suggestion) and `POST /api/ratings/{id}/dismiss`. A team's questions go when it is deleted.

## Using items in the field

A potion, an ether or a revive can be used outside a fight: `POST /api/heroes/{id}/use-item` (login and CSRF token; a strict body: the item's `position` and an optional `target_hero_id`, the user's own hero when left out) spends one of the item on one of the *caller's own* heroes (`terraforma.heroes.field.use_item`). What it does is `Rules.field_use(rng, effect, vitals, maximums)`: by default the item's own `use_effect` as in a fight (heal and restore mana for the living, revive for the dead; anything else is for fights only), and a game overrides it to allow more or less. The new HP and MP are kept on the hero (`Hero.vitals`). A hero in a fight that is still running can neither use nor receive an item (the fight has its own item command), and a use that would change nothing is refused and costs nothing. The roll comes from the world's stream for that hero, item and stack size, so it never uses the `random` module.

## Profiles

A player, a team and an alliance can each have a public page, reached by a **random token** (22 characters, from `secrets`) rather than a name or number, so a page can't be guessed. The owner can replace the token at any time; the old one stops working at once. A page shows only what its owner chose, and never an account id, username or email.

- **The player's page** (`GET /api/p/{token}`, no login): the player's public handle (`null` if none is set), a short bio of up to 500 characters, and the teams the player chose to list, each named and, only while the player has *team pages* on, with the token of its own page. It is always public to anyone holding the link. The owner makes it with `POST /api/profile/token` (which also replaces it), reads it with `GET /api/profile`, and sets `PUT /api/profile/bio` (`bio`), `PUT /api/profile/team-pages` (`enabled`, off by default: one switch for all the player's teams), `PUT /api/profile/team-alliances` (`enabled`, off by default: whether the team pages name the teams' alliances), `PUT /api/profile/team-listed` (`team_id`, `listed`: each team's own show/hide toggle) and `POST /api/profile/team-token` (`team_id`).
- **A team's page** (`GET /api/p/team/{token}`): the team's name, its player's handle and, only if its player turned *team alliances* on, the alliances it is in (each with its page token once the alliance has one; with the switch off the `alliances` key is absent). It exists only while its player has team pages on (otherwise 404).
- **An alliance's page** (`GET /api/p/alliance/{token}`): its name, a short description and *every* team in it with its role; a team carries a link only where its player has team pages on. Members read it with `GET /api/alliances/{id}/profile`; a team whose role may `speak` (`Alliances.permissions`) makes and replaces it with `POST .../profile/token` and writes it with `PUT .../profile/bio`. Members of an alliance can always open a member team's page, team pages on or not, with `GET /api/alliances/{id}/teams/{team_id}/profile` (a login; 404 to anyone else). No page links to a player's page except the player's own; the opt-in directory, where a player lets others find the page, comes later.

The owner's calls need a login and, to change anything, the CSRF token, and are limited to 60 an hour per account. The public pages need neither, and are limited per address (120 per 15 minutes) so a token can't be hunted for. Deleting a team or disbanding an alliance deletes its page. These routes and their response shapes are public interfaces (the license exception covers them).

## Tokens

Tokens are a currency whose meaning the game decides; the engine keeps each account's balance (`TokenBalance`) and a ledger of every change (`TokenEntry`: who, how many, why, which fight), so a balance can always be re-derived (`tokens.service.audit`). `Rules.tokens_earned(fight, address)` says what a hero's fighter earns when a fight ends; it pays nothing by default, so a game opts in. `fights.store.apply_results` pays it, once per account per fight (the ledger holds a unique fight and account pair, so saving the result again pays nothing more). A game spends through `tokens.service.change(session, account_id, -amount, reason)`, which refuses to take the balance below zero. Tokens are only ever paid to the account that owns a hero in the fight, so an admin (`Account.is_admin`) earns only when fighting as a player, never for a monster or a fight they watched. No call sets `is_admin`: a game's own setup does, with `tokens.service.set_admin`. `Rules.tokens_earned` and the `tokens.service` functions are public interfaces (the license exception covers them).

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
- **Experience, gold and advancement.** Whenever one fighter moves another's life or mana, `Rules.gauge_moved` makes the target *owe* the actor experience (an `XpDebt` event: the share of the gauge that moved, at the target's `Rules.pxp`, DragonStar's potential experience). When `Rules.fight_is_over` first holds (one party left, or only allies), `Rules.on_fight_end` pays the debts out with DragonStar's experience tree (`fights.experience`): half to whoever earned it, 20% pooled across its party and 30% across its team, for harm done to non-allies and help given to non-enemies; only fighters on a team (`Party.teams`) earn, monsters and NPCs only dilute the pools. Dead enemies' gold is split between each party's teams (`Gold` events), and `Rules.advance` levels up whoever has the experience (`LevelUp`: each stat grows 75 to 100% of the job's growth, from the fight's stream, up to `Rules.max_level`; `Rules.experience_needed` says how much each level takes). Override `on_fight_end` or `advance` for other rewards. Once the payout is made the fight is *over* (a `FightOver` event, and `Fight.over`): it plays no more rounds, so nothing is paid twice, and `fights.store.play_round` raises `FightOver` if asked for another. `fights.store.apply_results` then saves each hero's experience, level and stats to the database and teaches it the abilities its job grants at its new level (it sets rather than adds, so calling it again is harmless). It also saves what each hero's resources (HP, MP, ...) stand at in `Hero.vitals`, and `fights.build.hero_fighter` brings a hero into its next fight with them (never above the maximum in `Hero.stats`; a hero that has never fought, or been rested, is full). A hero that ended a fight at zero HP starts the next one dead. The engine never regenerates heroes between fights: a game calls `heroes.service.rest_hero(hero, rules)` from its inn, potion, camp or level rest, and `Rules.rest(vitals, maximums)` decides what that does, for the living and the dead alike. The default fills everything and revives the dead in full; a game overrides `rest` to bring them back at 1 HP, at half, or to heal only a part (a game that wants regeneration over time calls `rest_hero` itself). A team's heroes reach a fight with `fights.build.team_party`, which also gives the `Party.teams` the experience tree needs.
- **Monster AI.** `fights.ai.choose_command(rules, fight, address, rng)` picks a fighter's command from its four numbers (a monster's `ai` block: `action`, `goal`, `target`, `experience`; DragonStar's meanings, in `fights.ai`): what kind of player it is, what it wants, how well it judges its target, and how much it misjudges stats. `profile_for_level` gives a hero on autopilot its numbers. It draws only from the stream it is given, so fights replay. The engine adds what DragonStar left as notes: what statuses and stat changes are worth to the AI (`Rules.status_worth`, reckoned in hit points by `fights.ai_status`; override it for your own way) and its own play for the Protector (heals, cleanses and buffs the weak and afflicted), Hinderer (bad statuses and lowered stats first), Smart (weighs every command and takes the best) and Omnipotent (as Smart, seeing every stat exactly) actions. It departs from DragonStar in three places, each noted in `fights/ai.py`: stats are read skewed by the fighter's experience percent (DragonStar's formula reads them as a hundredth), a command that reaches neighbours adds up the neighbours' values, and the Fighter and Mage actions keep the better half of their targets, not the first half.
- **Dice.** Never the `random` module: every roll comes from a `WorldRng` stream, one per fight under its map (`fight_stream(world_rng, map_name, fight_id)`), drawn in a fixed order, so the same fight replays exactly.
- **Storing a fight** (`terraforma.fights.store`): a fight is its *initial state*, written once when it starts (`fights.state.dehydrate` turns a `Fight` into plain JSON and `hydrate` back), plus an append-only log of rounds. Each round records the commands that led to it, the events it produced, and a hash of the round before (the first chains from a hash of the initial state), so a changed or missing round shows. `load_state` rebuilds the fight now by replaying the log; `verify` checks the chain, and with `deep=True` plays every round again from its commands and its own dice (`fight_stream(world_rng, map, fight_id, round)`) and expects the same events. Anyone not given a command in a round defends. Heroes and monsters become fighters with `fights.build.hero_fighter` and `monster_fighter` (a hero starts a fight at full HP and MP for now).
- **Pure.** `terraforma.fights` (apart from `store` and `build`) has no database or web code. Heroes and monsters become fighters through `terraforma.fights.content`.

## Live fights

A fight waits for its round (`terraforma.fights.live`). **Starting** one is the server's business, not a player's call: `live.start_team_fight(session, team, monster_keys, rules)` puts a team against monsters where the team stands (the map's encounters will call it), so nobody picks their own opponents. A hero can be in one unfinished fight at a time.

- **Commands.** `POST /api/fights/{id}/commands` (login and CSRF token; a strict body: the fighter, the command, what is used, the target) commits a command for one of the *caller's own* heroes' living fighters; sending another replaces it. Nothing else can command a fighter: the monsters are on the AI.
- **Rounds.** The round plays as soon as every player's living fighter has committed, or when `Rules.round_seconds` (30 by default) run out, whichever comes first; a fighter that has not committed defends. The monsters choose with the AI (`fights.ai`) from a stream of their own under the round's, and the commands that were used are stored with the round, so replays never run the AI again. Time here is the wall clock (players are waiting); everything else is the fight's own dice.
- **Longer rounds.** A player who needs more time can choose a longer round (an accessibility setting): `GET /api/settings/round-time` (login) shows the choice, the round length it gives and what each option costs; `POST /api/settings/round-time` (login and CSRF token; the body is `{"multiplier": 1.5}`) chooses one. A game sets the choices and prices in `Rules.time_multipliers`, pairs of `(multiplier, bonus)`; DragonStar's are `1` for nothing, `1.5` for 5% and `2` for 10%, which is the default. The price is on the monsters: each gets that much more max HP (rounded up, in whole percents), so the fight is harder and pays more experience, since a monster's PXP follows its stats. A fight waits `Rules.round_length(multiplier)` for every round, with the longest multiplier anyone in it asked for (`fights.timing.fight_multiplier`), fixed when it starts; a choice counts for fights started after it. A server-started fight reads the team owner's choice; a game that starts fights with several players calls `timing.multiplier_for`, `fight_multiplier` and `toughen` the same way (a player on the monsters' side pays nothing: they are not the ones the monsters are made tougher for).
- **The timer.** The server looks for overdue rounds every `fight_timer_seconds` (settings, 1 by default; `live.resolve_overdue` does one pass).
- **The end.** The round that ends a fight saves its result to the heroes and pays its gold through the game's economy, once (`store.apply_results`), and the fight is finished.
- **Finding.** `GET /api/fights` (login; `?running=true` for the unfinished ones, `?limit=` 1 to 50, 20 by default) lists the caller's fights, running first then the most recent: the number (for the calls and the socket), the public name, the round it waits for, when it plays, and which of the caller's heroes are in it.
- **Looking.** `GET /api/fights/{id}` shows a fight to its players (who is standing with how much, which fighters are theirs and have committed, when the round plays); `GET /api/fights/watch/{guid}` shows it to anyone logged in who knows its public name.
- **The socket.** `/ws/fights/{id}` (the Origin header must be the site's own, and a login) pushes `committed` (who has committed, never what), then `round` (the round's events, whether the fight is over, and the next deadline). Only the fight's players may listen by its number; anyone logged in may with `?guid=`. The page only listens: commands go through the call.

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

## Housekeeping

`terraforma.housekeeping` tidies up what has gone stale. The server runs every job each `housekeeping_seconds` (settings, 3600 by default, 0 for never; `housekeeping.run(sessionmaker, settings)` does one pass). A job is an async function `(session, now, settings)` registered with `@housekeeping.job` that returns how many rows it removed; each runs in its own transaction, and a failing job is logged without stopping the others. A job deletes only what nothing reads any more, so a repeat or a second server is harmless. Today: `finished_rate_limit_windows` removes rate-limit counters whose window is over (a subject that never returns would keep its row otherwise). `old_closed_ballots` and `old_settled_rating_prompts` remove closed ballots (with their votes) and answered or dismissed rating prompts older than `ballot_retention_days` and `rating_prompt_retention_days`; both default to 0, which keeps them for good, so a game opts in through `settings.toml`. A pending prompt is never removed. `stale_pending_drops` settles a pending drop (see Held drops) that has waited longer than `pending_drop_timeout_seconds` (settings, 0 by default: it waits for ever): a need/want drop with the answers so far (hold-outs count as passing), a hand-out nobody made as `unclaimed`. Games cannot add jobs yet.

"""The play tests of the living world. Each is a story run on a game's real app (see ``harness``) that ends in checks.

A scenario gets the simulated world (``sim``), the cast found in it (``cast``), the options (``opts``) and a ``Result`` to say things to and
check against. What it expects comes from plain arithmetic and from the game's own hooks (``sim.game.realms``, ``.needs``, ``.market``),
never from the engine code it is exercising, so a game with other numbers is checked against its own rules.

Slice 5 of the living world (ruins closing their gates, events, strength from play, dormant villages) is not on the engine yet. Its
scenarios are written down at the end as pending: each names the one thing that has to exist and says what it will check, and runs as
skipped until ``features`` finds it.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from ...content.models import Item
from .cast import Cast, discover, transient_material, transient_realm, transient_town
from .report import Result, table


class Skip(Exception):
    """The scenario can't run on this game or engine; the message says why."""


@dataclass
class Options:
    hop: int = 10          # ticks between housekeeping passes
    limit: int = 20_000    # the most ticks a scenario lets pass waiting for something to happen
    rounds: int = 60       # rounds of the long run
    seed: int = 1          # the long run's own dice (a stream of the simulator, not of the world)
    shop: str = ""
    item: str = ""


@dataclass(frozen=True)
class Scenario:
    name: str
    about: str
    run: Callable
    needs: tuple[str, ...] = ()   # cast parts it needs, by name (``town``, ``hamlet``, ``shop_town`` ...)


SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str, about: str, needs: tuple[str, ...] = ()):
    def register(function):
        SCENARIOS[name] = Scenario(name, about, function, needs)
        return function

    return register


# -- small helpers -------------------------------------------------------------------------------------------------

def pct(row: dict) -> int:
    return row["level"] * 100 // row["capacity"] if row["capacity"] else 0


def clamp(low: int, value: int, high: int) -> int:
    return max(low, min(high, value))


def expected_level(level: int, capacity: int, rate: int, drain: int, cut: int, ticks: int) -> int:
    """What a supply holds after $ticks more ticks: the sources add rate (less the cut percent), use takes drain; kept from 0 to capacity."""
    return clamp(0, level + rate * (100 - cut) * ticks // 100 - drain * ticks, capacity)


def gap_for(sim, town: str, material: str) -> tuple[int, str]:
    """What the game's ``Needs`` says about this supply: the floor (percent) under which a quest is wanted, and the slot it goes in."""
    row = sim.supply(town, material)
    settlement, stuff = transient_town(sim.town(town)), transient_material(row)
    return sim.game.needs.supply_floor(settlement, stuff), sim.game.needs.supply_slot(settlement, stuff)


def open_for(sim, town: str, material: str) -> list[dict]:
    """The supply quests still wanted or on offer for this supply (proposed or live)."""
    return [each for each in sim.proposals() if each["status"] in ("proposed", "live") and each["reason"] == "supply"
            and each["spec"]["supply"]["settlement"] == town and each["spec"]["supply"]["material"] == material]


def census(sim, realm_key: str) -> dict[str, int]:
    state = sim.snapshot()["state"]
    own = {each["key"] for each in sim.world()["settlements"] if each["realm_id"] == sim.realm(realm_key)["id"]}
    return {kind: sum(1 for key in own if state[key] == kind) for kind in ("live", "ruins", "dormant")}


def need(cast: Cast, *parts: str) -> None:
    for part in parts:
        if not getattr(cast, part):
            raise Skip(f"this world has no {part} to play with" + (f" ({'; '.join(cast.notes)})" if cast.notes else ""))


def advance_until(sim, hop: int, limit: int, done: Callable[[], bool], housekeeping: bool = True) -> int:
    """Lets time pass hop by hop (a pass of housekeeping after each) until $done() or $limit ticks. Returns the ticks that passed."""
    passed = 0
    while passed < limit and not done():
        sim.advance(hop)
        if housekeeping:
            sim.housekeeping()
        passed += hop
    return passed


# -- supplies ------------------------------------------------------------------------------------------------------

@scenario("supply-clock", "a town's supply fills and drains with the world clock, and a raid (a full cut) stops it filling", needs=("town",))
def supply_clock(sim, cast: Cast, r: Result, opts: Options) -> None:
    town, material = cast.town, cast.material
    row = sim.supply(town, material)
    r.say(f"{town}/{material}: {row['level']} of {row['capacity']}, +{row['rate']} -{row['drain']} a tick, cut {row['cut']}%")
    rows = []
    for ticks in (1, 2, 3, 50):
        before = sim.supply(town, material)
        sim.advance(ticks)
        after = sim.supply(town, material)
        want = expected_level(before["level"], before["capacity"], before["rate"], before["drain"], before["cut"], ticks)
        rows.append([sim.tick, ticks, before["level"], after["level"], want])
        r.check(after["level"] == want, f"after {ticks} tick(s) the level is the sum of what came in and went out", f"saw {after['level']}, wanted {want}")
    r.say("\n".join(table(["tick", "+ticks", "was", "is", "wanted"], rows)))
    again = sim.supply(town, material)
    r.check(again == sim.supply(town, material), "looking twice at the same tick changes nothing")

    sim.cut(town, material, 100)
    before = sim.supply(town, material)
    sim.advance(15)
    raided = sim.supply(town, material)
    want = expected_level(before["level"], before["capacity"], before["rate"], before["drain"], 100, 15)
    r.check(raided["level"] == want and raided["level"] <= before["level"], "in a raid (cut 100%) nothing comes in, so the level never rises",
            f"{before['level']} -> {raided['level']}, wanted {want}")
    sim.cut(town, material, 50)
    half = sim.supply(town, material)
    sim.advance(15)
    want = expected_level(half["level"], half["capacity"], half["rate"], half["drain"], 50, 15)
    r.check(sim.supply(town, material)["level"] == want, "a half cut brings in half", f"wanted {want}")
    sim.cut(town, material, 0)

    full = row["rate"] > row["drain"]
    sim.advance(opts.limit)
    last = sim.supply(town, material)
    if full:
        r.check(last["level"] == last["capacity"], "left alone long enough a supply that gains is full, and never past full", f"{last['level']} of {last['capacity']}")
    elif row["drain"] > row["rate"]:
        r.check(last["level"] == 0, "left alone long enough a supply that loses is empty, and never below empty", f"{last['level']}")
    else:
        r.check(0 <= last["level"] <= last["capacity"], "a supply that holds stays within its capacity")


@scenario("supply-quest", "a raid starves a town; the world asks for a quest, a team hands goods in, and the quest is taken back", needs=("town",))
def supply_quest(sim, cast: Cast, r: Result, opts: Options) -> None:
    town, material = cast.town, cast.material
    floor, slot = gap_for(sim, town, material)
    if not floor:
        raise Skip(f"the game sets no supply floor for {town}/{material}")
    row = sim.supply(town, material)
    if row["drain"] == 0:
        raise Skip(f"{town}/{material} never loses anything, so a raid can't starve it")
    team = sim.team(sim.player("Courier").name)
    r.say(f"floor {floor}% (slot {slot or 'none'}); {town}/{material} starts at {pct(row)}%")
    r.check(not open_for(sim, town, material) or pct(row) < floor, "a healthy supply has no quest asked for it")

    sim.cut(town, material, 100)
    r.say("a raid cuts the sources")
    spent = advance_until(sim, opts.hop, opts.limit, lambda: pct(sim.supply(town, material)) < floor)
    low = sim.supply(town, material)
    r.say(f"{spent} ticks later it is at {pct(low)}%")
    if not r.check(pct(low) < floor, "the raid takes the supply under the floor", f"at {pct(low)}% after {spent} ticks"):
        return
    sim.housekeeping()
    asked = open_for(sim, town, material)
    if not slot:
        r.check(not asked, "with no slot to put it in, no quest is made")
        return
    if not r.check(len(asked) == 1, "exactly one quest is asked for the starved supply", f"{len(asked)}"):
        return
    ask = asked[0]
    r.check(ask["slot"] == slot, "it goes in the slot the game named", ask["slot"])
    r.say(f"quest: {ask['name']!r}, {ask['spec']['supply']['amount']} {material} ({ask['status']})")
    for _ in range(3):
        sim.advance(opts.hop)
        sim.housekeeping()
    r.check(len(open_for(sim, town, material)) == 1, "passes of housekeeping while the gap lasts do not ask for it again")
    if ask["status"] == "proposed":
        ask = sim.approve(ask["id"])
    r.check(ask["status"] == "live" and ask["template_id"], "the quest is on offer as a template of its slot", f"{ask['status']}")

    sim.cut(town, material, 0)
    before = sim.supply(town, material)
    added = sim.deliver(ask["spec"])
    after = sim.supply(town, material)
    want = min(ask["spec"]["supply"]["amount"], before["capacity"] - before["level"])
    r.check(added == want and after["level"] == before["level"] + want, "the team's hand-in adds what the quest asked, as far as it fits",
            f"{before['level']} -> {after['level']} (+{added}), wanted +{want}")
    sim.housekeeping()
    now_pct = pct(sim.supply(town, material))
    still = open_for(sim, town, material)
    if now_pct >= floor:
        r.check(not still, "once the supply is back over the floor the quest is taken back", f"{now_pct}% >= {floor}%")
    else:
        r.check(len(still) == 1, "a hand-in that leaves it under the floor leaves the quest open", f"{now_pct}% < {floor}%")
    del team


# -- shops ---------------------------------------------------------------------------------------------------------

def oracle_price(sim, shop: str, item_key: str, fullness: int) -> int | None:
    """What the game's market says the item costs at this fullness (None: sold out)."""
    market = sim.game.market

    async def ask(db):
        item = await db.scalar(select(Item).where(Item.key == item_key))
        percent = market.price_percent(shop, item, fullness)
        return None if percent is None else max(1, (market.price(shop, item) * percent + 50) // 100)

    return sim.run(ask)


@scenario("shop-prices", "as a supply runs low the shop's price rises, then the item sells out; restocking brings it back", needs=("shop_town",))
def shop_prices(sim, cast: Cast, r: Result, opts: Options) -> None:
    town, material, shop, item = cast.shop_town, cast.shop_material, cast.shop, cast.item
    row = sim.supply(town, material)
    if row["drain"] == 0:
        raise Skip(f"{town}/{material} never loses anything, so its shop prices never move")
    buyer = sim.team(sim.player("Shopper").name, gold=1_000_000)
    rows, seen = [], []
    sold_out_seen = False

    def look() -> tuple[int, int | None, int | None]:
        fullness = pct(sim.supply(town, material))
        wanted = oracle_price(sim, shop, item, fullness)
        wares = sim.shop(buyer, shop)
        got = wares.get(item)
        rows.append([sim.tick, fullness, "-" if got is None else got, "-" if wanted is None else wanted])
        r.check(got == wanted, f"at {fullness}% full the shop asks what the game's curve says", f"saw {got}, curve says {wanted}")
        return fullness, got, wanted

    def buy_check(why: str) -> None:
        pay, gold = sim.shop(buyer, shop)[item], sim.gold(buyer)
        sim.buy(buyer, item)
        r.check(gold - sim.gold(buyer) == pay, f"a purchase {why} costs exactly the asking price", f"asked {pay}, paid {gold - sim.gold(buyer)}")

    fullness, price, _ = look()
    first = price
    if price is not None:
        buy_check("at full stock")
    dear_checked = False
    sim.cut(town, material, 100)
    for _ in range(opts.limit // opts.hop):
        if row["capacity"] and pct(sim.supply(town, material)) == 0:
            break
        sim.advance(max(1, opts.hop))
        fullness, price, wanted = look()
        seen.append((fullness, price))
        if price is not None and first is not None and price > first and not dear_checked:
            dear_checked = True
            buy_check("when it is dear")
        sold_out_seen = sold_out_seen or price is None
    r.say("\n".join(table(["tick", "full %", "asks", "curve"], rows[:: max(1, len(rows) // 25)])))
    on_sale = [(f, p) for f, p in seen if p is not None]
    r.check(all(later[1] >= sooner[1] for sooner, later in zip(on_sale, on_sale[1:])), "while on sale the price only ever rises as the supply falls")
    if first is not None and on_sale:
        r.check(max(p for _, p in on_sale) >= first, "the price at the lowest stock is at least the full-stock price", f"{first} -> {max(p for _, p in on_sale)}")
    if sold_out_seen or oracle_price(sim, shop, item, 0) is None:
        gone = sim.shop(buyer, shop)
        if r.check(item not in gone, "at an empty supply the shop no longer sells the item"):
            answer = sim.buy(buyer, item, expect=(409, 422))
            r.check(answer.status_code in (409, 422), "and refuses to sell it", f"{answer.status_code}")
    else:
        r.say("the game's curve never sells this item out; no sell-out to check")

    sim.cut(town, material, 0)
    sim.deliver({"supply": {"settlement": town, "material": material, "amount": row["capacity"]}})
    fullness, price, wanted = look()
    r.check(fullness == 100 and (first is None or price == first), "after a full restock the price is back to the full-stock price", f"{price} vs {first}")
    paid = sim.gold(buyer)
    sim.buy(buyer, item)
    r.check(sim.gold(buyer) < paid, "and the item can be bought again")


# -- realms --------------------------------------------------------------------------------------------------------

def fullness_of(sim, realm_key: str) -> list[int]:
    """How full each supply of each live town of the realm is, in whole percent, at the world clock now."""
    realm_id = sim.realm(realm_key)["id"]
    return [pct(row) for town in sim.world()["settlements"] if town["realm_id"] == realm_id and town["state"] == "live" for row in sim.supplies(town["key"])]


def as_of(sim, realm_key: str) -> int:
    from ...realms.models import Realm

    async def ask(db):
        return (await db.scalar(select(Realm).where(Realm.world_id == sim.world_id, Realm.key == realm_key))).as_of_tick

    return sim.run(ask)


@scenario("realm-drift", "a realm's strength drifts toward its game's target by at most the step each tick; admin and play nudge it", needs=("realm",))
def realm_drift(sim, cast: Cast, r: Result, opts: Options) -> None:
    key, hooks = cast.realm, sim.game.realms
    sim.housekeeping()  # (brings the strength up to the clock)
    rows = []
    for start, ticks in ((30, 1), (30, 5), (-500, 40), (900, 3)):
        sim.housekeeping()
        sim.set_strength(key, start)
        sim.advance(ticks)
        realm, elapsed = sim.realm(key), sim.tick - as_of(sim, key)
        target = hooks.target(transient_realm(realm), fullness_of(sim, key))
        sim.housekeeping()
        after = sim.realm(key)["strength"]
        if target is None:
            want = start
        else:
            move = min(abs(target - start), max(0, hooks.step) * elapsed)
            want = start + move if target > start else start - move
        rows.append([start, elapsed, "-" if target is None else target, after, want])
        r.check(after == want, f"from {start} after {elapsed} ticks it moves toward {target} by at most {hooks.step} a tick", f"saw {after}, wanted {want}")
    r.say("\n".join(table(["from", "ticks", "target", "is", "wanted"], rows)))

    base = sim.set_strength(key, 100)["strength"]
    r.check(base == 100, "the admin's call sets the strength")
    r.check(sim.nudge(key, 7) == 107 and sim.nudge(key, -20) == 87, "a play outcome nudges the strength up and down")
    r.check(sim.nudge(key, 10**9) == 1_000_000 and sim.nudge(key, -10**9) == -1_000_000, "and a nudge never takes it past the limits")
    r.check(sim.call(sim.admin().name, "POST", f"/api/admin/realms/{sim.realm(key)['id']}/strength", (422,), strength=10**9).status_code == 422,
            "the admin call refuses a strength past the limits")
    sim.set_strength(key, 100)


@scenario("fall-and-found", "a failing realm loses towns to ruins (and its teams forget them); a strong one wakes dormant villages, one a pass",
          needs=("realm", "hamlet"))
def fall_and_found(sim, cast: Cast, r: Result, opts: Options) -> None:
    key, hooks = cast.realm, sim.game.realms
    floor = -1_000_000
    hamlet = transient_town(sim.town(cast.hamlet))
    if not hooks.falls(transient_realm({**sim.realm(key), "strength": floor}), hamlet):
        raise Skip(f"the game never lets {cast.hamlet} fall, however weak {key} is")
    team = sim.team(sim.player("Wanderer").name)
    realm_id = sim.realm(key)["id"]
    own = lambda: [each for each in sim.world()["settlements"] if each["realm_id"] == realm_id]  # noqa: E731
    for each in own():
        if each["state"] == "live":
            sim.visit(team, each["key"])
    r.say(f"the team has visited {sim.visited(team)}")
    trace = []

    def note(label):
        counts = census(sim, key)
        trace.append([sim.tick, label, sim.realm(key)["strength"], counts["live"], counts["ruins"], counts["dormant"]])

    sim.housekeeping()
    note("start")
    live_before = {each["key"] for each in own() if each["state"] == "live"}
    sim.set_strength(key, floor)
    sim.advance(opts.hop)
    sim.housekeeping()
    weak = sim.realm(key)
    live_after = {each["key"] for each in own() if each["state"] == "live"}
    fell = live_before - live_after
    should = {each["key"] for each in own() if each["key"] in live_before and hooks.falls(transient_realm(weak), transient_town(each))}
    note("collapse")
    r.say(f"strength {weak['strength']}: fell {sorted(fell)}")
    r.check(fell == should, "exactly the towns the game says fall at that strength fall", f"fell {sorted(fell)}, game says {sorted(should)}")
    r.check(all(sim.town(each)["state"] == "ruins" for each in fell), "a fallen town is kept as ruins, not deleted")
    r.check(not set(sim.visited(team)) & fell, "and it leaves the team's visited list", f"visited {sim.visited(team)}")
    r.check(set(sim.visited(team)) == live_after & set(sim.visited(team)) or set(sim.visited(team)) <= live_after, "the towns still standing stay on it")
    sim.housekeeping()
    for each in fell:
        mats = [row["material"] for row in sim.supplies(each)]
        r.check(all(not open_for(sim, each, material) for material in mats), f"no supply quest stays open for {each}, in ruins")
        for material in mats:
            r.check(sim.deliver({"supply": {"settlement": each, "material": material, "amount": 10}}) == 0, f"goods handed in for ruined {each} are not taken")
            break

    sim.set_strength(key, -floor)
    waking = [each["key"] for each in sorted(own(), key=lambda each: each["key"]) if each["state"] == "dormant"]
    waking += [each["key"] for each in sorted(own(), key=lambda each: each["key"]) if each["state"] == "ruins"]
    r.say(f"to wake, in the order the world should: {waking}")
    woken = []
    for number in range(1, len(waking) + 3):
        sim.advance(1)
        sim.set_strength(key, -floor)
        before = {each["key"]: each["state"] for each in own()}
        sim.housekeeping()
        strong = sim.realm(key)
        founded = [each["key"] for each in own() if each["state"] == "live" and before[each["key"]] != "live"]
        note(f"pass {number}")
        r.check(len(founded) <= 1, f"pass {number} founds at most one town", f"{founded}")
        pool = [k for k in waking if before[k] != "live"]
        candidates = sorted((k for k in pool if before[k] == "dormant")) + sorted((k for k in pool if before[k] == "ruins"))
        wanted = next((k for k in candidates if hooks.founds(strong and transient_realm(strong), transient_town(sim.town(k)))), None)
        r.check(founded == ([wanted] if wanted else []), f"pass {number} founds the first the game allows, dormant villages before ruins", f"founded {founded}, wanted {wanted}")
        woken += founded
    r.say("\n".join(table(["tick", "event", "strength", "live", "ruins", "dormant"], trace)))
    r.check(set(woken) == set(waking) or not all(hooks.founds(transient_realm(sim.realm(key)), transient_town(sim.town(k))) for k in waking),
            "a strong realm in the end raises everything it can")
    if cast.dormant:
        r.check(cast.dormant in woken or sim.town(cast.dormant)["state"] == "live", "the dormant village is among the live towns again")
    sim.set_strength(key, 60)


@scenario("long-run", "weeks of play: raids, quests handed in, shoppers, drift, falls and foundings, with the world's rules checked after every round", needs=("realm",))
def long_run(sim, cast: Cast, r: Result, opts: Options) -> None:
    dice = random.Random(opts.seed)
    players = [sim.team(sim.player(f"Crew{number}").name, gold=500) for number in range(1, 4)]
    start = sim.snapshot()
    hooks = sim.game.realms
    raids: dict[tuple[str, str], int] = {}
    totals = {"raids": 0, "asked": 0, "handed": 0, "goods": 0, "sold": 0, "sold_out": 0, "fell": 0, "founded": 0}
    seen_asks: set[int] = set()
    trace, violations = [], []
    sold_out_at = lambda: (oracle_price(sim, cast.shop, cast.item, pct(sim.supply(cast.shop_town, cast.shop_material))) is None)  # noqa: E731
    last_states = start["state"]
    for number in range(1, opts.rounds + 1):
        live = [(t["key"], row["material"]) for t in sim.world()["settlements"] if t["state"] == "live" for row in sim.supplies(t["key"])]
        for pair in [pair for pair, left in raids.items() if left <= 0]:
            sim.cut(*pair, 0)
            del raids[pair]
        for pair in raids:
            raids[pair] -= 1
        if live and dice.random() < 0.3:
            pair = dice.choice(live)
            if pair not in raids:
                sim.cut(*pair, dice.choice((60, 100)))
                raids[pair] = dice.randint(2, 6)
                totals["raids"] += 1
        sim.advance(opts.hop)
        sim.housekeeping()
        for ask in sim.proposals():
            if ask["reason"] == "supply" and ask["id"] not in seen_asks:
                seen_asks.add(ask["id"])
                totals["asked"] += 1
        for who in players:
            wanted = [ask for ask in sim.proposals("live") if ask["reason"] == "supply"]
            if wanted and dice.random() < 0.6:
                ask = dice.choice(wanted)
                got = sim.deliver(ask["spec"])
                totals["handed"] += 1
                totals["goods"] += got
                realm = next(t for t in sim.world()["settlements"] if t["key"] == ask["spec"]["supply"]["settlement"])
                realm_key = next(each["key"] for each in sim.world()["realms"] if each["id"] == realm["realm_id"])
                sim.nudge(realm_key, max(1, got // 10))
            if cast.shop_town and dice.random() < 0.5:
                if sold_out_at():
                    totals["sold_out"] += 1
                elif sim.gold(who) >= 1000 or sim.set_gold(who, 500) is None:
                    sim.shop(who, cast.shop)
                    sim.buy(who, cast.item, expect=(200, 409, 422))
                    totals["sold"] += 1
        snap = sim.snapshot()
        totals["fell"] += sum(1 for k, s in snap["state"].items() if last_states[k] == "live" and s != "live")
        totals["founded"] += sum(1 for k, s in snap["state"].items() if last_states[k] != "live" and s == "live")
        last_states = snap["state"]
        # the world's rules, after every round
        for t in sim.world()["settlements"]:
            for row in sim.supplies(t["key"]):
                if not 0 <= row["level"] <= row["capacity"]:
                    violations.append(f"round {number}: {t['key']}/{row['material']} holds {row['level']} of {row['capacity']}")
        for realm in sim.world()["realms"]:
            if not -1_000_000 <= realm["strength"] <= 1_000_000:
                violations.append(f"round {number}: {realm['key']} strength {realm['strength']}")
            for t in sim.world()["settlements"]:
                if t["realm_id"] == realm["id"] and t["state"] == "live" and hooks.falls(transient_realm(realm), transient_town(t)):
                    violations.append(f"round {number}: {t['key']} should have fallen at {realm['key']} strength {realm['strength']}")
        per_supply: dict[tuple[str, str], int] = {}
        for ask in sim.proposals():
            if ask["status"] in ("proposed", "live") and ask["reason"] == "supply":
                pair = (ask["spec"]["supply"]["settlement"], ask["spec"]["supply"]["material"])
                per_supply[pair] = per_supply.get(pair, 0) + 1
        violations += [f"round {number}: {pair} has {count} open quests" for pair, count in per_supply.items() if count > 1]
        for who in players:
            ruined = {k for k, s in snap["state"].items() if s == "ruins"}
            stale = set(sim.visited(who)) & ruined
            violations += [f"round {number}: a team still lists the ruins {k}" for k in stale]
        if number % max(1, opts.rounds // 10) == 0 or number == opts.rounds:
            counts = {kind: sum(1 for s in snap["state"].values() if s == kind) for kind in ("live", "ruins", "dormant")}
            average = sum(snap["fullness"].values()) // max(1, len(snap["fullness"]))
            trace.append([number, snap["tick"], *(snap["strength"][k] for k in sorted(snap["strength"])), counts["live"], counts["ruins"], counts["dormant"],
                          average, len(raids), totals["handed"]])
    end = sim.snapshot()
    head = ["round", "tick", *sorted(end["strength"]), "live", "ruins", "dormant", "avg full %", "raids on", "hand-ins"]
    r.say("\n".join(table(head, trace)))
    r.say("totals: " + ", ".join(f"{k} {v}" for k, v in totals.items()))
    moved = [f"{k}: {start['state'][k]} -> {s}" for k, s in sorted(end["state"].items()) if start["state"][k] != s]
    r.say("towns that changed state: " + (", ".join(moved) or "none"))
    r.check(not violations, "after every round: levels within capacity, strengths within limits, nothing left that should have fallen, one open quest per supply, "
            "no ruins on a team's list", "; ".join(violations[:5]) + (f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""))
    if totals["asked"]:
        r.check(totals["handed"] > 0, "supply quests that were asked for got handed in")
    r.check(totals["raids"] > 0, "the run had raids (so the rest was exercised under stress)", f"{totals['raids']}")


# -- slice 5: written down, waiting for the engine -----------------------------------------------------------------

PENDING = {
    "ruins-closed": ("slice 5, engine A", "a settlement in ruins or dormant closes its gate, NPCs and shops (a ``Towns`` hook default); founding reverses it.",
                     "A team walks to the ruins of a fallen town: the gate refuses, its NPCs do not answer, its shop sells nothing and its inn does not rest "
                     "the team; after founding it again the same walk works."),
    "world-events": ("slice 5, engine B", "timed supply cuts the world makes itself (raids on weak realms) and a notice to teams.",
                     "A weak realm gets a raid event with a duration; supplies fall during it and recover after; teams of the realm get a notice; a strong "
                     "realm gets none; the raid ends on the world clock, not before."),
    "strength-from-play": ("slice 5, Vanguard C", "hand-ins, guild quests and fights raise or lower a realm's strength.",
                           "A team hands in goods for a supply quest and its realm's strength rises by the game's amount; a team fighting the realm's "
                           "enemies moves it; a team failing a quest moves it down; all by the game's own amounts."),
    "dormant-villages": ("slice 5, Vanguard D", "a pool of dormant villages per kingdom, woken as the kingdom grows.",
                         "Each kingdom has dormant villages with maps, NPCs and supplies; a strong kingdom wakes one per housekeeping pass, in the "
                         "authored order, and a team can walk into it."),
}


def make_pending(name: str) -> Scenario:
    slice_name, about, plan = PENDING[name]

    def run(sim, cast, r: Result, opts: Options):
        from . import features

        if not features.found(name, sim):
            raise Skip(f"not on this engine yet ({slice_name}). It will check: {plan}")
        raise Skip(f"{name} is on this engine ({features.FOUND[name]}) but its play test is not written yet. It should check: {plan}")

    return Scenario(name, f"(pending, {slice_name}) {about}", run)


for _name in PENDING:
    SCENARIOS[_name] = make_pending(_name)

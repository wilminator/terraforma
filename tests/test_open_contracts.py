"""Open contracts: the guild posts, the first team to take one holds it, the guild pays or keeps the escrow, on every database."""

import pytest

from terraforma.contracts import service
from terraforma.contracts.models import ACTIVE, BREACHED, BROKEN, EXPIRED, FULFILLED, MET, OFFERED, PENDING, Contract
from terraforma.escrow import service as escrow
from terraforma.escrow.models import FORFEITED, REFUNDED, Escrow

from .test_contracts import ECONOMY, FEES, PACTS, STORAGE, Contracts, clock, DAY, gold_of, two_sides  # noqa: F401

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


class Guildwork(Contracts):
    """One open kind, `slay`, whose verdict a test sets."""

    def __init__(self):
        self.verdict = PENDING

    def kinds(self):
        return {"slay"}

    def open_kinds(self):
        return {"slay"}

    async def check(self, session, contract, term, ended):
        return self.verdict


async def posted(db, contracts, **over):
    args = {"guild": "vanguard", "terms": [("slay", {"who": "wolf"})], "escrow_gold": 100, "reward_gold": 300, "duration": DAY, **over}
    return await service.post_open(db, contracts, **args)


async def test_the_guild_posts_and_the_first_team_to_take_it_holds_it(clock, db):
    a, b, aria, cole = await two_sides(db)
    work = Guildwork()
    contract = await posted(db, work)
    assert (contract.state, contract.a_team_id, contract.b_team_id, contract.reward_gold) == (OFFERED, None, None, 300)
    assert [c.id for c in await service.open_offers(db, "vanguard")] == [contract.id]
    taken = await service.accept(db, ECONOMY, FEES, work, aria, contract.id)
    assert (taken.state, taken.b_team_id) == (ACTIVE, a.id)
    assert await gold_of(db, a) == 1000 - 10 - 100
    with pytest.raises(service.ContractError, match="gone"):
        await service.accept(db, ECONOMY, FEES, work, cole, contract.id)
    assert await gold_of(db, b) == 1000
    assert await service.open_offers(db) == []


async def test_a_kept_contract_pays_the_reward_and_returns_the_escrow(clock, db):
    a, _b, aria, _cole = await two_sides(db)
    work = Guildwork()
    contract = await posted(db, work)
    await service.accept(db, ECONOMY, FEES, work, aria, contract.id)
    work.verdict = MET
    done = await service.judge(db, ECONOMY, STORAGE, work, contract.id)
    assert (done.state, done.breached_by) == (FULFILLED, None)
    assert await gold_of(db, a) == 1000 - 10 + 300


async def test_a_broken_contract_loses_the_escrow_to_the_guild_and_pays_nothing(clock, db):
    a, _b, aria, _cole = await two_sides(db)
    work = Guildwork()
    contract = await posted(db, work)
    taken = await service.accept(db, ECONOMY, FEES, work, aria, contract.id)
    escrow_id = taken.b_escrow_id
    work.verdict = BROKEN
    done = await service.judge(db, ECONOMY, STORAGE, work, contract.id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
    assert await gold_of(db, a) == 1000 - 10 - 100
    held = await db.get(Escrow, escrow_id)
    assert (held.state, held.settled_to) == (FORFEITED, None)


async def test_an_untaken_contract_expires_and_only_the_games_kinds_are_allowed(clock, db):
    await two_sides(db)
    work = Guildwork()
    contract = await posted(db, work)
    clock.move(4 * DAY)
    assert await service.expire_offers(db, ECONOMY, STORAGE) == 1
    await db.refresh(contract)
    assert contract.state == EXPIRED
    for kwargs, why in (
        ({"terms": [("truce", {})]}, "open term"), ({"terms": []}, "binds someone"), ({"duration": 0}, "runs from"),
        ({"reward_gold": -1}, "zero or more"), ({"guild": ""}, "needs a guild"),
    ):
        with pytest.raises(service.ContractError, match=why):
            await posted(db, work, **kwargs)
    with pytest.raises(service.ContractError, match="open term"):
        await posted(db, PACTS)  # PACTS lists no open kinds


async def test_the_guild_keeps_only_gold(db):
    a, _b, aria, _cole = await two_sides(db)
    held = await escrow.hold(db, ECONOMY, aria, "vanguard", "test", "1", gold=50)
    kept = await escrow.keep(db, held.id)
    assert (kept.state, kept.settled_to) == (FORFEITED, None)
    with pytest.raises(escrow.EscrowError, match="settled already"):
        await escrow.keep(db, held.id)
    assert await gold_of(db, a) == 950

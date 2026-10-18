"""The alliance's treasury: deposits, spending, and the split of what is left when the alliance dissolves."""

import pytest
from sqlalchemy import func, select, update

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.alliances import ballots, service, treasury
from terraforma.alliances.hooks import Alliances
from terraforma.alliances.models import Alliance, AllianceAlumnus
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Team
from terraforma.models import Account

pytestmark = pytest.mark.social

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
RULES = Alliances()
MONTH = RULES.share_month


async def setup(db, count=2):
    """An alliance of a commander (Lead) and ``count`` more teams (T1, ...), each with a hero, 1,000 gold and active."""
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    lead = await heroes.create_team(db, mike, "Lead")
    teams = [lead]
    for number in range(1, count + 1):
        teams.append(await heroes.create_team(db, mike, f"T{number}"))
    for team in teams:
        await heroes.add_to_team(db, mike, team.id, (await heroes.create_hero(db, mike, f"{team.name} hero", "fighter")).id)
    alliance = await service.found(db, RULES, lead, "Pact")
    for team in teams[1:]:
        await service.invite(db, RULES, alliance, lead.id, team.id)
        await service.accept(db, RULES, team, alliance.id)
    await db.execute(update(Team).values(last_played_at=wallclock.timestamp(), gold=1000))
    return alliance, teams


async def purse(db, alliance):
    return await db.scalar(select(Alliance.gold).where(Alliance.id == alliance.id))


async def gold(db, *teams):
    return [await db.scalar(select(Team.gold).where(Team.id == team.id)) for team in teams]


# --- deposits and spending -----------------------------------------------------------------------------------------------------

@anyio
async def test_a_deposit_leaves_the_teams_gold_and_fills_the_treasury(db):
    alliance, (lead, one, two) = await setup(db)
    assert await service.deposit(db, RULES, alliance, one.id, 300) == 300
    assert await service.deposit(db, RULES, alliance, lead.id, 50) == 350
    assert await gold(db, lead, one, two) == [950, 700, 1000] and alliance.gold == 350
    for amount, message in ((0, "1 to"), (treasury.MAX_AMOUNT + 1, "1 to"), (701, "not enough gold")):
        with pytest.raises(treasury.TreasuryError, match=message):
            await service.deposit(db, RULES, alliance, one.id, amount)
    assert await gold(db, one) == [700] and alliance.gold == 350, "a refused deposit changes nothing"


@anyio
async def test_a_deposit_is_a_gift_nothing_returns_when_the_team_leaves(db):
    alliance, (lead, one, two) = await setup(db)
    await service.deposit(db, RULES, alliance, one.id, 300)
    await service.leave(db, RULES, alliance, one.id)
    assert await gold(db, one) == [700] and alliance.gold == 300


@anyio
async def test_a_role_that_holds_spend_pays_a_member_team_from_the_treasury(db):
    alliance, (lead, one, two) = await setup(db)
    await service.deposit(db, RULES, alliance, two.id, 500)
    await service.appoint(db, RULES, alliance, lead.id, one.id, "treasurer")
    assert await service.spend(db, RULES, alliance, one.id, two.id, 200) == 300
    assert await gold(db, two) == [700]
    with pytest.raises(service.Forbidden):
        await service.spend(db, RULES, alliance, two.id, two.id, 1)  # a member holds deposit, not spend
    with pytest.raises(treasury.TreasuryError, match="doesn't hold"):
        await service.spend(db, RULES, alliance, one.id, two.id, 301)
    outsider = await heroes.create_team(db, await db.get(Account, lead.account_id), "Guest")
    with pytest.raises(service.NotFound):
        await service.spend(db, RULES, alliance, one.id, outsider.id, 1)
    assert alliance.gold == 300


@anyio
async def test_a_rule_can_ask_for_a_vote_before_the_treasury_is_spent(db):
    alliance, (lead, one, two) = await setup(db)
    await service.deposit(db, RULES, alliance, two.id, 500)
    await service.set_rule(db, RULES, alliance, lead.id, "spend", "", 60)
    with pytest.raises(service.NeedsVote):
        await service.spend(db, RULES, alliance, lead.id, one.id, 100)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "spend", {"to_team_id": one.id, "amount": 100})
    assert (ballot.title, await purse(db, alliance)) == ("Spend T1 100", 500)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    assert ballot.result["executed"] and await purse(db, alliance) == 400 and (await gold(db, one)) == [1100]


# --- shares ----------------------------------------------------------------------------------------------------------------------------

def test_shares_fall_by_one_for_each_month_since_a_team_played_and_end_after_a_year():
    now = 10 * 365 * 24 * 3600
    got = [treasury.shares(RULES, now - age, now) for age in (0, MONTH - 1, MONTH, 2 * MONTH, 11 * MONTH, 12 * MONTH - 1, 12 * MONTH, 30 * MONTH)]
    assert got == [12, 12, 11, 10, 1, 1, 0, 0]
    assert treasury.shares(RULES, None, now) == 0, "a team that never played"
    assert treasury.shares(RULES, now + 100, now) == 12, "(a clock that ran backwards)"


@anyio
async def test_a_dissolving_alliance_pays_its_treasury_by_shares_to_every_team_that_ever_belonged(db):
    alliance, (lead, one, two) = await setup(db)
    await service.deposit(db, RULES, alliance, one.id, 1000)
    now = wallclock.timestamp()
    await service.remove(db, RULES, alliance, lead.id, two.id)  # an alumnus now
    assert await db.scalar(select(func.count()).select_from(AllianceAlumnus)) == 1
    await db.execute(update(Team).where(Team.id == one.id).values(last_played_at=now - 2 * MONTH))  # 10 shares
    await db.execute(update(Team).where(Team.id == two.id).values(last_played_at=now - 5 * MONTH))  # 7 shares
    # Lead played just now: 12 shares. 1,000 gold over 29 shares: 413, 344 and 241; 2 coins are left for the heaviest.
    await service.disband(db, RULES, alliance, lead.id)
    assert await gold(db, lead, one, two) == [1000 + 414, 0 + 345, 1000 + 241]
    assert await db.scalar(select(func.count()).select_from(AllianceAlumnus)) == 0


@anyio
async def test_the_gold_nobody_has_a_share_of_leaves_the_economy(db):
    alliance, (lead, one) = await setup(db, 1)
    await service.deposit(db, RULES, alliance, one.id, 400)
    await db.execute(update(Team).values(last_played_at=wallclock.timestamp() - 13 * MONTH))
    await service.disband(db, RULES, alliance, lead.id)
    assert await gold(db, lead, one) == [1000, 600]


@anyio
async def test_the_last_team_out_dissolves_the_alliance_and_gets_the_treasury(db):
    alliance, (lead, one) = await setup(db, 1)
    await service.deposit(db, RULES, alliance, one.id, 400)
    await service.hand_over(db, RULES, alliance, lead.id, one.id)
    await service.leave(db, RULES, alliance, lead.id)
    assert await service.leave(db, RULES, alliance, one.id) is True
    assert await db.scalar(select(func.count()).select_from(Alliance)) == 0
    assert await gold(db, lead, one) == [1000 + 200, 600 + 200], "both shared the 400, equally active"


@anyio
async def test_a_team_that_rejoins_is_a_member_again_not_an_alumnus(db):
    alliance, (lead, one) = await setup(db, 1)
    await service.leave(db, RULES, alliance, one.id)
    assert await db.scalar(select(func.count()).select_from(AllianceAlumnus)) == 1
    await service.invite(db, RULES, alliance, lead.id, one.id)
    await service.accept(db, RULES, one, alliance.id)
    assert await db.scalar(select(func.count()).select_from(AllianceAlumnus)) == 0

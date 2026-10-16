"""Escrow: a guild holds a team's gold, tokens and items, then refunds or forfeits them, all or nothing, on every database."""

import pytest
from sqlalchemy import func, select

from terraforma.challenge import service as challenge
from terraforma.economy import TeamGold
from terraforma.escrow import service as escrow
from terraforma.escrow.models import FORFEITED, HELD, REFUNDED, RELEASED, Escrow, EscrowItem
from terraforma.heroes import inventory, service
from terraforma.heroes.models import Team
from terraforma.storage.hooks import NoStorage, TeamStorage

from .test_trading import account, holding, stock, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()


async def two_teams(db):
    """Two teams of one account each (Aria's and Cole's), the first with 100 gold, 10 tokens, two potions and a sword."""
    mike = await account(db)
    mine, (aria,) = await team_with(db, mike, "Vanguard", ["Aria"])
    yours, (cole,) = await team_with(db, mike, "Rearguard", ["Cole"])
    await ECONOMY.credit_team(db, mine.id, 100)
    await challenge.change(db, mike.id, 10, "test")
    await stock(db, aria, "potion", 2)
    await stock(db, aria, "sword")
    return mike, mine, yours, aria, cole


async def gold_of(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


async def deposit(db, aria, **what):
    return await escrow.hold(db, ECONOMY, aria, "vanguard", "breach", "contract-1", **what)


# --- holding -----------------------------------------------------------------------------------------------------------

async def test_a_deposit_takes_gold_tokens_and_items_and_keeps_them_with_the_guild(db):
    mike, mine, _yours, aria, _cole = await two_teams(db)
    held = await deposit(db, aria, gold=40, tokens=3, items=[(0, 2)])
    assert (held.guild, held.purpose, held.ref, held.team_id, held.state) == ("vanguard", "breach", "contract-1", mine.id, HELD)
    assert await gold_of(db, mine) == 60
    assert await challenge.balance(db, mike.id) == 7
    assert await holding(db, aria, "potion") == 0 and await holding(db, aria, "sword") == 1
    assert await escrow.contents(db, held) == {"gold": 40, "tokens": 3, "items": [("potion", 2)]}
    assert [row.id for row in await escrow.held(db, team_id=mine.id)] == [held.id]


async def test_held_deposits_can_be_found_by_team_guild_purpose_and_ref(db):
    _mike, mine, yours, aria, cole = await two_teams(db)
    await ECONOMY.credit_team(db, yours.id, 5)
    first = await deposit(db, aria, gold=10)
    other = await escrow.hold(db, ECONOMY, cole, "crown", "listing", "item-9", gold=5)
    assert [row.id for row in await escrow.held(db)] == [first.id, other.id]
    assert [row.id for row in await escrow.held(db, team_id=yours.id)] == [other.id]
    assert [row.id for row in await escrow.held(db, guild="vanguard")] == [first.id]
    assert [row.id for row in await escrow.held(db, purpose="listing")] == [other.id]
    assert [row.id for row in await escrow.held(db, ref="contract-1")] == [first.id]
    assert await escrow.held(db, ref="nothing") == []


async def test_a_short_purse_holds_nothing_and_takes_nothing(db):
    mike, mine, _yours, aria, _cole = await two_teams(db)
    with pytest.raises(escrow.EscrowError, match="gold"):
        await deposit(db, aria, gold=101, tokens=3, items=[(0, 2)])
    with pytest.raises(escrow.EscrowError, match="tokens"):
        await deposit(db, aria, gold=40, tokens=11, items=[(0, 2)])
    assert await gold_of(db, mine) == 100, "the gold of the second try came back"
    assert await challenge.balance(db, mike.id) == 10
    assert await holding(db, aria, "potion") == 2
    assert await db.scalar(select(func.count()).select_from(Escrow)) == 0


@pytest.mark.parametrize("items, why", [
    ([(5, 1)], "nothing in that position"),
    ([(0, 3)], "you have 2"),
    ([(0, 0)], "at least 1"),
    ([(0, 1), (0, 1)], "named once"),
])
async def test_bad_stacks_are_refused_and_change_nothing(db, items, why):
    _mike, mine, _yours, aria, _cole = await two_teams(db)
    with pytest.raises(escrow.EscrowError, match=why):
        await deposit(db, aria, gold=10, items=items)
    assert await gold_of(db, mine) == 100 and await holding(db, aria, "potion") == 2


async def test_a_worn_item_is_not_held(db):
    _mike, mine, _yours, aria, _cole = await two_teams(db)
    position = next(stack.position for stack, item in await inventory.stacks(db, aria) if item.key == "sword")
    await inventory.equip(db, aria, position)
    with pytest.raises(escrow.EscrowError, match="take it off"):
        await deposit(db, aria, gold=10, items=[(position, 1)])
    assert await gold_of(db, mine) == 100


async def test_an_empty_deposit_and_a_hero_with_no_team_are_refused(db):
    mike, _mine, _yours, aria, _cole = await two_teams(db)
    with pytest.raises(escrow.EscrowError, match="nothing to hold"):
        await deposit(db, aria)
    with pytest.raises(escrow.EscrowError, match="zero or more"):
        await deposit(db, aria, gold=-1)
    with pytest.raises(escrow.EscrowError, match="guild and a purpose"):
        await escrow.hold(db, ECONOMY, aria, "", "breach", gold=1)
    loner = await service.create_hero(db, mike, "Loner", "fighter")
    with pytest.raises(escrow.EscrowError, match="on a team"):
        await escrow.hold(db, ECONOMY, loner, "vanguard", "breach", gold=1)


# --- settling ----------------------------------------------------------------------------------------------------------

async def test_a_refund_gives_everything_back_to_the_depositing_team(db):
    mike, mine, _yours, aria, _cole = await two_teams(db)
    held = await deposit(db, aria, gold=40, tokens=3, items=[(0, 2)])
    settled = await escrow.refund(db, ECONOMY, NoStorage(), held.id)
    assert (settled.state, settled.settled_to) == (REFUNDED, mine.id) and settled.settled_at
    assert await gold_of(db, mine) == 100
    assert await challenge.balance(db, mike.id) == 10 and await challenge.audit(db, mike.id)
    assert await holding(db, aria, "potion") == 2
    assert await escrow.held(db) == []


async def test_a_forfeit_gives_everything_to_the_other_team_and_nothing_back(db):
    mike, mine, yours, aria, cole = await two_teams(db)
    held = await deposit(db, aria, gold=40, tokens=3, items=[(0, 2)])
    settled = await escrow.forfeit(db, ECONOMY, NoStorage(), held.id, yours.id)
    assert (settled.state, settled.settled_to) == (FORFEITED, yours.id)
    assert await gold_of(db, mine) == 60 and await gold_of(db, yours) == 40
    assert await challenge.balance(db, mike.id) == 10, "both teams are Mike's, so the tokens only moved"
    assert await holding(db, aria, "potion") == 0 and await holding(db, cole, "potion") == 2


async def test_forfeited_tokens_go_to_the_receiving_teams_account(db):
    mike, _mine, _yours, aria, _cole = await two_teams(db)
    zed = await account(db, "Zed")
    zara_team, (_zara,) = await team_with(db, zed, "Zeds", ["Zara"])
    held = await deposit(db, aria, tokens=4)
    await escrow.forfeit(db, ECONOMY, NoStorage(), held.id, zara_team.id)
    assert await challenge.balance(db, mike.id) == 6 and await challenge.balance(db, zed.id) == 4


async def test_items_are_delivered_through_the_games_storage(db):
    _mike, _mine, yours, aria, cole = await two_teams(db)
    held = await deposit(db, aria, items=[(0, 2)])
    await escrow.forfeit(db, ECONOMY, TeamStorage(), held.id, yours.id)
    assert await holding(db, cole, "potion") == 0, "the potions went to the team's storage, not the pack"
    from terraforma.storage import service as storage_service

    owner = await TeamStorage().owner(db, cole)
    assert [(item.key, stack.qty) for stack, item in await storage_service.stacks(db, owner)] == [("potion", 2)]


async def test_a_deposit_is_settled_once(db):
    _mike, mine, yours, aria, _cole = await two_teams(db)
    escrow_id = (await deposit(db, aria, gold=40)).id
    await escrow.refund(db, ECONOMY, NoStorage(), escrow_id)
    for settle in (escrow.refund(db, ECONOMY, NoStorage(), escrow_id), escrow.forfeit(db, ECONOMY, NoStorage(), escrow_id, yours.id)):
        with pytest.raises(escrow.EscrowError, match="settled already"):
            await settle
    assert await gold_of(db, mine) == 100 and await gold_of(db, yours) == 0
    with pytest.raises(escrow.EscrowError, match="no such deposit"):
        await escrow.refund(db, ECONOMY, NoStorage(), escrow_id + 100)


async def test_a_team_with_no_room_gets_nothing_and_the_deposit_stays_held(db):
    mike, mine, yours, aria, cole = await two_teams(db)
    for _ in range(inventory.MAX_ITEMS):
        await stock(db, cole, "sword")  # equipment never stacks: Cole's pack is full
    held = await deposit(db, aria, gold=40, tokens=3, items=[(0, 2)])
    escrow_id = held.id
    with pytest.raises(escrow.EscrowError, match="no room"):
        await escrow.forfeit(db, ECONOMY, NoStorage(), escrow_id, yours.id)
    assert await gold_of(db, yours) == 0, "the gold that was paid before the items were tried came back"
    assert await challenge.balance(db, mike.id) == 7
    await db.refresh(held)  # the rolled-back settlement expired it
    assert held.state == HELD and held.settled_to is None
    assert await escrow.contents(db, held) == {"gold": 40, "tokens": 3, "items": [("potion", 2)]}
    await escrow.refund(db, ECONOMY, NoStorage(), escrow_id)  # the depositor still has room
    assert await gold_of(db, mine) == 100


async def test_a_team_with_no_hero_cannot_receive(db):
    mike, _mine, _yours, aria, _cole = await two_teams(db)
    empty = await service.create_team(db, mike, "Empty")
    held = await deposit(db, aria, gold=40)
    escrow_id = held.id  # a failed settlement expires the object, and its id with it
    with pytest.raises(escrow.EscrowError, match="no hero"):
        await escrow.forfeit(db, ECONOMY, NoStorage(), escrow_id, empty.id)
    with pytest.raises(escrow.EscrowError, match="no hero"):
        await escrow.forfeit(db, ECONOMY, NoStorage(), escrow_id, 9999)
    await db.refresh(held)
    assert held.state == HELD
    assert await db.scalar(select(func.count()).select_from(EscrowItem)) == 0
    assert isinstance(empty, Team)


async def test_releasing_a_deposit_hands_its_contents_to_the_caller_and_pays_nothing(db):
    _mike, mine, yours, aria, _cole = await two_teams(db)
    held = await deposit(db, aria, gold=40, items=[(0, 2)])
    escrow_id = held.id
    assert await escrow.release(db, escrow_id, yours.id) == {"gold": 40, "tokens": 0, "items": [("potion", 2)]}
    await db.refresh(held)
    assert (held.state, held.settled_to) == (RELEASED, yours.id)
    assert await holding(db, aria, "potion") == 0  # nothing was paid out: the caller sends it on
    for settle in (escrow.release(db, escrow_id), escrow.refund(db, ECONOMY, NoStorage(), escrow_id)):
        with pytest.raises(escrow.EscrowError, match="settled already"):
            await settle
    with pytest.raises(escrow.EscrowError, match="no such deposit"):
        await escrow.release(db, 9999)

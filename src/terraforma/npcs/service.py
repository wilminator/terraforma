"""Talking to NPCs: a hero stands where the game's ``Npcs.can_talk`` allows, and the NPC's dialog runs on the server, one step
at a time (``npcs.script``). The server decides everything: what is said, what is asked and where an answer goes; the browser
shows text and cues, and sends back the index of an answer. A hero is in at most one conversation, and in none while in a
fight that is still running.
"""

from functools import partial

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.field import in_running_fight
from ..heroes.models import Hero, Team, TeamMember
from ..parties import service as parties
from .hooks import Npcs
from .models import Npc, NpcTalk
from .state import DialogState
from .script import MAX_TEXT, BadAnswer, Script, ScriptError, Who, advance, parse

MAX_COUNTER = 64


class NpcError(ValueError):
    """Something the caller can fix: the message says what."""


class NoSuchNpc(NpcError):
    """There is no such NPC."""


async def place_npc(session: AsyncSession, key: str, name: str, map_id: int, x: int, y: int, dialog: str, counter: list[tuple[int, int]] | None = None) -> Npc:
    """Stands an NPC on a map at a tile with its dialog and counter tiles (a game's setup code calls it). The dialog is checked
    (ScriptError says what is wrong with it), and the NPC with this $key is moved and rewritten if it exists, so setup can run
    again."""
    if len(dialog) > MAX_TEXT:
        raise ScriptError(f"a dialog is at most {MAX_TEXT} characters")
    parse(dialog)
    if len(counter or []) > MAX_COUNTER:
        raise NpcError(f"a counter is at most {MAX_COUNTER} tiles")
    tiles = [[int(tile_x), int(tile_y)] for tile_x, tile_y in (counter or [])]
    npc = await session.scalar(select(Npc).where(Npc.key == key))
    if npc is None:
        npc = Npc(key=key, name=name, map_id=map_id, x=x, y=y, dialog=dialog, counter=tiles)
        session.add(npc)
    else:
        npc.name, npc.map_id, npc.x, npc.y, npc.dialog, npc.counter = name, map_id, x, y, dialog, tiles
    await session.flush()
    return npc


async def npcs_here(session: AsyncSession, hooks: Npcs, hero: Hero) -> list[Npc]:
    """The NPCs on the hero's map the hero could talk to from where they stand."""
    rows = await session.scalars(select(Npc).where(Npc.map_id == hero.map_id).order_by(Npc.name, Npc.id))
    return [npc for npc in rows.all() if await hooks.can_talk(session, npc, hero) is None]


async def who_is(session: AsyncSession, hero: Hero) -> Who:
    """What the dialog's team and party tags see of the hero: every team of the player's, and the hero's party."""
    teams = (await session.scalars(select(Team.id).where(Team.account_id == hero.account_id))).all()
    mine = (await session.scalars(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))).all()
    party_id = None
    for team_id in mine:
        party = await parties.party_of(session, team_id)
        if party is not None:
            party_id = party.id
            break
    return Who(frozenset(teams), party_id)


def public(prompt: dict | None) -> dict | None:
    """The prompt as the browser sees it: what to show and what the server will take next (``accepts``: the option indexes it
    takes as ``choice``, whether plain Next is taken, and whether the choice may be cancelled with a null ``choice``), not where
    each answer goes. A choice can always be cancelled; where that goes is the text's business."""
    if prompt is None:
        return None
    shown = {"type": prompt["type"]}
    if prompt["type"] == "choice":
        options = [{key: value for key, value in option.items() if key != "label"} for option in prompt["options"]]
        shown |= {"kind": prompt["kind"], "options": options, "accepts": {"choice": list(range(len(options))), "next": False, "cancel": True}}
    else:
        if prompt["type"] == "activity":
            shown |= {"command": prompt["command"], "parts": prompt["parts"]}
        shown["accepts"] = {"choice": [], "next": True, "cancel": False}
    return shown


def frame(npc: Npc, events: list[dict], prompt: dict | None) -> dict:
    return {"npc": {"id": npc.id, "key": npc.key, "name": npc.name}, "events": events, "prompt": public(prompt), "ended": prompt is None}


async def _talk_of(session: AsyncSession, hero: Hero) -> NpcTalk | None:
    return await session.scalar(select(NpcTalk).where(NpcTalk.hero_id == hero.id))


async def _may_talk(session: AsyncSession, hooks: Npcs, npc: Npc, hero: Hero) -> None:
    if await in_running_fight(session, hero):
        raise NpcError(f"{hero.name} is in a fight")
    if reason := await hooks.can_talk(session, npc, hero):
        raise NpcError(reason)


async def _run(session: AsyncSession, hooks: Npcs, hero: Hero, npc: Npc, talk: NpcTalk | None, choice: int | None) -> dict:
    script: Script = parse(npc.dialog)
    who = await who_is(session, hero)
    try:
        result = await advance(script, talk.pos if talk else 0, talk.prompt if talk else None, choice, who, partial(hooks.tag, session, hero), DialogState(session, hero))
    except ScriptError as error:
        if talk is not None and not isinstance(error, BadAnswer):
            await session.delete(talk)
            await session.flush()
        raise NpcError(str(error)) from error
    if result["prompt"] is None:
        if talk is not None:
            await session.delete(talk)
    elif talk is None:
        session.add(NpcTalk(hero_id=hero.id, npc_id=npc.id, pos=result["pos"], prompt=result["prompt"]))
    else:
        talk.pos, talk.prompt = result["pos"], result["prompt"]
    await session.flush()
    return frame(npc, result["events"], result["prompt"])


async def talk(session: AsyncSession, hooks: Npcs, hero: Hero, npc_id: int) -> dict:
    """The hero starts talking to the NPC (leaving any other conversation): what it says first, and what it asks."""
    npc = await session.get(Npc, npc_id)
    if npc is None:
        raise NoSuchNpc("there's no such person")
    await _may_talk(session, hooks, npc, hero)
    await session.execute(delete(NpcTalk).where(NpcTalk.hero_id == hero.id))
    return await _run(session, hooks, hero, npc, None, None)


async def answer(session: AsyncSession, hooks: Npcs, hero: Hero, choice: int | None) -> dict:
    """The hero goes on: Next (no $choice), or the index of the answer picked (None cancels where a prompt can be cancelled).
    The hero must still be where they can talk, or the conversation ends."""
    current = await _talk_of(session, hero)
    if current is None:
        raise NpcError("not in a conversation")
    npc = await session.get(Npc, current.npc_id)
    try:
        await _may_talk(session, hooks, npc, hero)
    except NpcError:
        await session.delete(current)
        await session.flush()
        raise
    return await _run(session, hooks, hero, npc, current, choice)


async def current(session: AsyncSession, hero: Hero) -> dict:
    """The conversation the hero is in (what it last asked), or ``{"talking": False}``."""
    row = await _talk_of(session, hero)
    if row is None:
        return {"talking": False}
    return {"talking": True, **frame(await session.get(Npc, row.npc_id), [], row.prompt)}


async def leave(session: AsyncSession, hero: Hero) -> None:
    """The hero walks away from whatever they were saying."""
    await session.execute(delete(NpcTalk).where(NpcTalk.hero_id == hero.id))

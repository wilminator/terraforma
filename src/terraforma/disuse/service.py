"""Force disuse for one hero: what it has to stop using, the scene that settles it, and what comes back.

Everything here is lazy and per hero. A commit only marks content rows ``disused``; nothing happens to a hero until its player asks
(``status``, ``start``, ``answer``), so a player who never returns costs nothing and the moment they do, whatever is disused then is
what they settle. A scene is a dialog the game wrote (``hooks.Disuse.scene``) run with ``npcs.script.advance``; ending it sets aside
the abilities and items the hero still has to stop using. A job is the player's to replace, and a scene that ends without a new job leaves
the hero blocked (``gate.blocked``) until one does.
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, Item, Job
from ..economy import Economy
from ..fights.rules import Rules
from ..heroes import inventory
from ..heroes.field import in_running_fight
from ..heroes.models import Hero, HeroAbility, HeroItem
from ..npcs import service as npcs
from ..npcs.hooks import Npcs
from ..npcs.script import BadAnswer, ScriptError, advance, parse
from .hooks import Disuse, Lost
from .models import ABILITY, ITEM, JOB, DisuseTalk, SetAside


class DisuseError(ValueError):
    """Something the caller can fix: the message says what."""


# --- what a hero has to stop using -----------------------------------------------------------------------------

async def lost(session: AsyncSession, hero: Hero) -> Lost:
    """The disused job, abilities and items the hero holds now."""
    job = await session.get(Job, hero.job_id)
    abilities = (await session.execute(
        select(Ability.key, Ability.name).join(HeroAbility, HeroAbility.ability_id == Ability.id)
        .where(HeroAbility.hero_id == hero.id, Ability.disused.is_(True)).order_by(Ability.name)
    )).all()
    items = (await session.execute(
        select(Item.key, Item.name, func.sum(HeroItem.qty)).join(HeroItem, HeroItem.item_id == Item.id)
        .where(HeroItem.hero_id == hero.id, Item.disused.is_(True)).group_by(Item.key, Item.name).order_by(Item.name)
    )).all()
    return Lost(job=(job.key, job.name) if job.disused else None, abilities=tuple((key, name) for key, name in abilities),
                items=tuple((key, name, int(qty)) for key, name, qty in items))


async def note(session: AsyncSession, hero: Hero, kind: str, key: str, qty: int) -> None:
    """Keeps $qty more of what the hero stopped using (a record of the job, an ability, items), added to what is already kept."""
    held = await session.scalar(select(SetAside).where(SetAside.hero_id == hero.id, SetAside.kind == kind, SetAside.key == key))
    if held is None:
        session.add(SetAside(hero_id=hero.id, kind=kind, key=key, qty=qty))
    else:
        held.qty += qty
    await session.flush()


async def set_aside(session: AsyncSession, hero: Hero, found: Lost) -> None:
    """Puts away the $found abilities and items: the hero no longer knows or carries them, and they are kept (``SetAside``). What the hero
    wore comes off with it. Nothing is deleted for good."""
    for key, _name in found.abilities:
        ability = await session.scalar(select(Ability).where(Ability.key == key))
        gone = await session.execute(delete(HeroAbility).where(HeroAbility.hero_id == hero.id, HeroAbility.ability_id == ability.id))
        if gone.rowcount:
            await note(session, hero, ABILITY, key, 1)
    for key, _name, _qty in found.items:
        taken = 0
        while (found := next(((stack, item) for stack, item in await inventory.stacks(session, hero) if item.key == key), None)) is not None:
            taken += await inventory.remove_item(session, hero, found[0].position, found[0].qty)  # (the stacks are renumbered each time)
        if taken:
            await note(session, hero, ITEM, key, taken)


@dataclass
class Restored:
    abilities: list[str] = field(default_factory=list)
    #: (name, how many came back); the rest waits for room in the pack.
    items: list[tuple[str, int]] = field(default_factory=list)

    def view(self) -> dict:
        return {"abilities": self.abilities, "items": [{"name": name, "qty": qty} for name, qty in self.items]}


async def _kept(session: AsyncSession, hero: Hero) -> list[tuple[SetAside, str, str]]:
    """What is kept for the hero and could come back: (record, name, kind), those whose content row is no longer disused."""
    found = []
    for table, kind in ((Ability, ABILITY), (Item, ITEM)):
        rows = await session.execute(
            select(SetAside, table.name).join(table, table.key == SetAside.key)
            .where(SetAside.hero_id == hero.id, SetAside.kind == kind, table.disused.is_(False)).order_by(table.name)
        )
        found += [(record, name, kind) for record, name in rows.all()]
    return found


async def restorable(session: AsyncSession, hero: Hero) -> list[dict]:
    """What is kept for the hero that would come back now (``restore``)."""
    return [{"kind": kind, "name": name, "qty": record.qty} for record, name, kind in await _kept(session, hero)]


async def restore(session: AsyncSession, hero: Hero) -> Restored:
    """Gives the hero back what was set aside and is no longer disused: abilities at once, items as far as the pack has room (the rest
    stays kept until there is)."""
    back = Restored()
    for record, name, kind in await _kept(session, hero):
        if kind == ABILITY:
            ability = await session.scalar(select(Ability).where(Ability.key == record.key))
            if await session.scalar(select(HeroAbility.id).where(HeroAbility.hero_id == hero.id, HeroAbility.ability_id == ability.id)) is None:
                session.add(HeroAbility(hero_id=hero.id, ability_id=ability.id))
            await session.delete(record)
            back.abilities.append(name)
        else:
            left = await inventory.add_item(session, hero, record.key, record.qty)
            if record.qty - left:
                back.items.append((name, record.qty - left))
            if left:
                record.qty = left
            else:
                await session.delete(record)
    await session.flush()
    return back


# --- the scene -----------------------------------------------------------------------------------------------

async def _talk_of(session: AsyncSession, hero: Hero) -> DisuseTalk | None:
    return await session.scalar(select(DisuseTalk).where(DisuseTalk.hero_id == hero.id))


def frame(events: list[dict], prompt: dict | None, pending: bool, restored: Restored | None = None) -> dict:
    """What the browser gets of a step: what happened (``events``, which may stage a ``scene``), what is asked, whether the scene ended and
    whether the hero still has something to settle (a scene that ended without a new job leaves it so)."""
    shown = {"events": events, "prompt": npcs.public(prompt), "ended": prompt is None, "pending": pending}
    return shown | ({"restored": restored.view()} if restored is not None else {})


async def status(session: AsyncSession, hero: Hero) -> dict:
    """What the hero has to settle (``pending``, ``lost``), what would come back (``restorable``) and the scene it is in, if any."""
    found, talk = await lost(session, hero), await _talk_of(session, hero)
    return {"pending": not found.empty, "lost": found.view(), "restorable": await restorable(session, hero),
            "talking": talk is not None, "prompt": npcs.public(talk.prompt) if talk is not None else None}


async def _run(session: AsyncSession, disuse: Disuse, game_npcs: Npcs, hero: Hero, talk: DisuseTalk | None, text: str, choice: int | None,
               economy: Economy | None, rules: Rules | None) -> dict:
    from .state import DisuseState  # (state reads this module)

    async def tag(command: str, parts: list[str]) -> str | None:
        return await game_npcs.tag(session, hero, command, parts)

    try:
        script = parse(text)
        who = await npcs.who_is(session, hero)
        async with session.begin_nested():  # a step is all or nothing, as in an NPC's talk
            result = await advance(script, talk.pos if talk else 0, talk.prompt if talk else None, choice, who, tag,
                                   DisuseState(session, hero, disuse, economy, rules), talk.tree if talk else None, talk.stack if talk else (), npcs.tree_loader(session))
    except ScriptError as error:
        if talk is not None and not isinstance(error, BadAnswer):
            await session.delete(talk)
            await session.flush()
        raise DisuseError(str(error)) from error
    if result["prompt"] is None:
        if talk is not None:
            await session.delete(talk)
        await set_aside(session, hero, await lost(session, hero))
    elif talk is None:
        session.add(DisuseTalk(hero_id=hero.id, text=text, pos=result["pos"], prompt=result["prompt"], tree=result["tree"], stack=result["stack"]))
    else:
        talk.pos, talk.prompt, talk.tree, talk.stack = result["pos"], result["prompt"], result["tree"], result["stack"]
    await session.flush()
    return frame(result["events"], result["prompt"], not (await lost(session, hero)).empty)


async def start(session: AsyncSession, disuse: Disuse, game_npcs: Npcs, hero: Hero, economy: Economy | None = None, rules: Rules | None = None) -> dict:
    """The hero's scene begins (a scene it was in starts over): first what is kept for it comes back, then the game's scene runs for what it
    still has to stop using. A hero with nothing to settle gets ``ended`` at once, with what came back."""
    if await in_running_fight(session, hero):
        raise DisuseError(f"{hero.name} is in a fight")
    await session.execute(delete(DisuseTalk).where(DisuseTalk.hero_id == hero.id))
    back = await restore(session, hero)
    found = await lost(session, hero)
    if found.empty:
        return frame([], None, False, back)
    try:
        text = await disuse.scene(session, hero, found)
        parse(text)
    except ScriptError as error:
        raise DisuseError(f"the game's scene is not valid dialog: {error}") from error
    return {**await _run(session, disuse, game_npcs, hero, None, text, None, economy, rules), "restored": back.view()}


async def answer(session: AsyncSession, disuse: Disuse, game_npcs: Npcs, hero: Hero, choice: int | None, economy: Economy | None = None,
                 rules: Rules | None = None) -> dict:
    """The hero goes on in its scene: Next (no $choice) or the answer picked (None cancels where a prompt can be cancelled)."""
    talk = await _talk_of(session, hero)
    if talk is None:
        raise DisuseError("not in a scene: start it first")
    return await _run(session, disuse, game_npcs, hero, talk, talk.text, choice, economy, rules)

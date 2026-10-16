"""What a game decides about force disuse. A game subclasses ``Disuse`` and hands it to ``Game(disuse=...)``. This is a public interface
(the license exception covers it).

When the player of a hero that holds something disused signs in, the hero goes through a *scene*: a dialog (``terraforma.npcs.script``)
the game writes entirely, so a game that wants a dream world, a god's voice or a letter from the guild draws it with its own ``scene``
tags and client code. The default scene is an all-black screen and a dialog written as if the player were asleep.

    class Disuse(terraforma.disuse.hooks.Disuse):
        async def scene(self, session, hero, lost):
            return "`include,the_long_dream`"
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Job
from ..heroes.models import Hero


@dataclass(frozen=True)
class Lost:
    """What a hero has to stop using: the job (key, name) if it is disused, the abilities (key, name) and the items (key, name, how many)."""

    job: tuple[str, str] | None = None
    abilities: tuple[tuple[str, str], ...] = ()
    items: tuple[tuple[str, str, int], ...] = ()

    @property
    def empty(self) -> bool:
        return self.job is None and not self.abilities and not self.items

    def view(self) -> dict:
        return {"job": None if self.job is None else {"key": self.job[0], "name": self.job[1]},
                "abilities": [{"key": key, "name": name} for key, name in self.abilities],
                "items": [{"key": key, "name": name, "qty": qty} for key, name, qty in self.items]}


def plain(text: str) -> str:
    """A name as dialog text: no backtick (it would open a tag) and no line break."""
    return " ".join(text.replace("`", "'").split())


def listed(names: list[str]) -> str:
    """``a``, ``a and b`` or ``a, b and c``."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class Disuse:
    async def jobs(self, session: AsyncSession, hero: Hero, lost: Lost) -> list[Job]:
        """The jobs the hero may take when its own is disused, in the order they are offered: every active job by default. (Only asked when
        ``lost.job`` is set; the engine refuses ``set_job`` for any job not listed.)"""
        return list((await session.scalars(select(Job).where(Job.active.is_(True)).order_by(Job.id))).all())

    async def scene(self, session: AsyncSession, hero: Hero, lost: Lost) -> str:
        """The dialog text of the hero's scene, for what it has $lost. It may use every dialog tag, the engine's ``scene``, ``set_job`` and
        ``lost``, and ``include`` of the game's own trees. When it ends, the abilities and items the hero still has to stop using are set
        aside with it, and a hero whose job is still disused goes through the scene again at its next sign-in.

        The default: a black screen and a dream. It says what the hero loses (set aside, safe, to come back if it ever returns) and, for
        a job, asks for a new one: the hero keeps its level, experience and stats."""
        text = ["`scene,black`Everything is dark and quiet. You are asleep, and you know it, the way you do in a dream.\n"
                "`ack`While you were away, the people who keep this world changed it, and they are sorry for the trouble it causes you.\n`ack`"]
        gone = [plain(name) for _key, name in lost.abilities] + [plain(name) + (f" (x{qty})" if qty > 1 else "") for _key, name, qty in lost.items]
        if gone:
            text.append(f"You can't use {listed(gone)} any more. They are put away, safe, and come back to you, whole, if they ever return to the world.\n`ack`")
        if lost.job is not None:
            offered = await self.jobs(session, hero, lost)
            name = plain(lost.job[1])
            if not offered:
                text.append(f"Your class, {name}, is gone from the world, and no other is open to you yet. Rest, and come back another day.\n`end`")
            else:
                options = ",".join(f"{plain(job.name).replace(',', ' ')},pick_{number}" for number, job in enumerate(offered))
                text.append(f"Your class, {name}, is gone from the world. To wake and go on you must choose a new class. Your level and all you earned stay with you.\n"
                            f"`label,choose``question,{options},choose`Which class will you be?\n")
                text += [f"`label,pick_{number}``set_job,{job.key},again``jump,done`" for number, job in enumerate(offered)]
                text.append("`label,again`That class is not open to you now.\n`jump,choose``label,done`")
        text.append("A light comes up slowly. You wake.")
        return "".join(text)

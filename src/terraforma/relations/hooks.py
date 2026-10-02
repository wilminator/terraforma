"""The game's say in relationships: ``Relations``, the class a game overrides (``Game(relations=...)``).

A relationship is how one side (a team, later an alliance) feels about another, as a score from -100 to 100 and a
note only its author reads. It is *directed*: A's view of B and B's view of A are separate. The engine never changes
a score on its own, and nothing it stores changes except through ``Relations.resolve``, so the game decides every
change: what a player may set, what an event moves (a game calls ``service.apply`` from its own rules), and who may
form a relationship at all (``may_form``). The defaults accept what is asked, within the scale.

    class Grudges(Relations):
        async def resolve(self, session, subject, object, current, change):
            if change.by == "player" and change.score is not None and change.score > current + 20:
                return current + 20          # trust is earned a little at a time
            return await super().resolve(session, subject, object, current, change)

The bands name stretches of the scale (``enemy`` ... ``close`` by default); a game sets its own with ``bands``.

This is a public interface (the license exception covers it): the names and signatures are what games build on.
"""

from dataclasses import dataclass

SCORE_MIN, SCORE_MAX = -100, 100


@dataclass(frozen=True)
class Ref:
    """One side of a relationship: a team (``kind`` "team") by its id."""

    kind: str
    id: int


@dataclass(frozen=True)
class Band:
    """A named stretch of the scale, ``low`` to ``high`` inclusive."""

    name: str
    low: int
    high: int


DEFAULT_BANDS = (
    Band("enemy", -100, -61),
    Band("wary", -60, -21),
    Band("neutral", -20, 20),
    Band("friendly", 21, 60),
    Band("close", 61, 100),
)


@dataclass(frozen=True)
class Change:
    """A requested change to ``subject``'s view of ``object``: to the score ``score``, or by ``delta`` from where it is.
    ``by`` says who asked: "player" (the owner, in a call) or "game" (the game's own rules); ``reason`` is for the game's use."""

    subject: Ref
    object: Ref
    score: int | None = None
    delta: int | None = None
    by: str = "player"
    reason: str = ""


def clamp(score: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(score)))


class Relations:
    bands: tuple[Band, ...] = DEFAULT_BANDS

    def band(self, score: int) -> str:
        """The name of the band the score is in."""
        for band in self.bands:
            if band.low <= score <= band.high:
                return band.name
        return ""

    async def may_form(self, session, subject: Ref, object: Ref, change: Change) -> bool:
        """Whether ``subject`` may start a relationship with ``object`` (the first time a score or note is set). Yes by default."""
        return True

    async def initial(self, session, subject: Ref, object: Ref) -> int:
        """Where a new relationship starts: neutral, by default."""
        return 0

    async def resolve(self, session, subject: Ref, object: Ref, current: int, change: Change) -> int:
        """The score after ``change``, given ``current``: the one place a score changes. By default what was asked,
        within the scale (an absolute ``score``, or ``delta`` from ``current``)."""
        if change.score is not None:
            return clamp(change.score)
        return clamp(current + (change.delta or 0))

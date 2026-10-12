"""What the game's monster-play hooks (``Rules.may_queue_monsters``, ``Rules.may_play_monsters``) are told.

These are public interfaces (the license exception covers them): a game's rules read these fields.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Volunteer:
    """Someone who wants to command monsters. ``account_id`` is None for a person who is not logged in (a guest: the engine's own
    calls always have an account, so for now only a game's own code makes one). ``team_ids`` are the teams the account owns,
    ``in_fight`` says whether it has a hero in a fight that has not ended (a hero that fled does not count) or sits in one as a
    monster player, and ``locked_until`` is when a lockout still running ends (None when there is none).
    Admins are told apart because the rules treat them differently (``is_admin``)."""

    account_id: int | None
    is_admin: bool = False
    team_ids: tuple[int, ...] = ()
    in_fight: bool = False
    locked_until: datetime | None = None


@dataclass(frozen=True)
class Seating:
    """What the engine found out about a volunteer and the new fight they might be seated in: ``has_hero`` (the account owns a
    hero in the fight) and ``allied`` (one of the account's teams counts a team in the fight's hero parties as an ally,
    as judged by ``Relations.stance`` across all the account's teams)."""

    fight_id: int
    has_hero: bool = False
    allied: bool = False

"""Helping another party: using an item from a hero's pack, or an ability the hero knows, on a hero of a party in reach.

The hero must be in range of the party for ``help`` (``Reach.party``), and the hero's team must not count the party as an enemy
(``Rules.may_help`` on the stance ``Rules.party_stance`` gives, which forms the relationships the teams lack). A party's own
heroes are in reach of one another by the same rule. What the item does is the field use of ``terraforma.heroes.field``; helping
asks for no answer from the other player, since it only gives. The target is named by its place in the party (``hero_ids``
order), so a player never learns the id of a hero in a team they cannot see.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights import joining
from ..fights.rules import Rules
from ..heroes import field
from ..heroes.field import FieldUseError
from ..heroes.models import Hero, TeamMember
from ..parties import service as parties
from ..parties.models import Party
from ..relations.hooks import Relations
from .hooks import HELP, Reach
from .service import party_of_hero


class HelpError(FieldUseError):
    """The help can't be given: the message says why."""


async def helped_hero(session: AsyncSession, reach: Reach, rules: Rules, relations: Relations, user: Hero, party_id: int, member: int) -> tuple[Party, Hero]:
    """The party $user may help and the hero at place $member of it: in reach for ``help`` and not an enemy of the user's team (a party's own
    members need no one's leave). Raises HelpError with the reason."""
    try:
        party = await parties.get_party(session, party_id)
    except parties.NotFound as error:
        raise HelpError(str(error)) from error
    if reason := await reach.party(session, HELP, user, party):
        raise HelpError(reason)
    hero_ids = await parties.hero_ids(session, party.id)
    if not 0 <= member < len(hero_ids):
        raise HelpError("there's no such member of that party")
    target = await session.get(Hero, hero_ids[member])
    mine = await party_of_hero(session, user)
    if mine is None or mine.id != party.id:  # a party's own members need no one's leave to be helped
        team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == user.id))
        if team_id is None:
            raise HelpError("put the hero on a team first")
        stance = await joining.stance_toward(session, rules, relations, [team_id], await parties.team_ids(session, party.id))
        if reason := rules.may_help(stance):
            raise HelpError(reason)
    return party, target


async def help_with_item(
    session: AsyncSession, reach: Reach, rules: Rules, relations: Relations, user: Hero, position: int, party_id: int, member: int
) -> dict:
    """$user spends the item at $position of its pack on the hero at place $member of party $party_id. Returns the party, the member and the target's
    resources before and after. Raises HelpError (or FieldUseError, from the use itself) with the reason, changing nothing."""
    party, target = await helped_hero(session, reach, rules, relations, user, party_id, member)
    result = await field.use_item(session, user, position, target, rules, helping=True)
    result.pop("hero_id")  # the player named the member by its place, and learns no id
    return {"party_id": party.id, "member": member, **result}


async def help_with_ability(
    session: AsyncSession, reach: Reach, rules: Rules, relations: Relations, user: Hero, ability: str, party_id: int, member: int
) -> dict:
    """$user casts the ability it knows on the hero at place $member of party $party_id. Returns the party, the member, the target's resources before
    and after and what the cast cost. Raises HelpError (or FieldUseError, from the cast itself) with the reason, changing nothing."""
    party, target = await helped_hero(session, reach, rules, relations, user, party_id, member)
    result = await field.use_ability(session, user, ability, target, rules, helping=True)
    result.pop("hero_id")
    return {"party_id": party.id, "member": member, **result}

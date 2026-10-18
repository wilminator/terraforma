"""The game's say in alliances: ``Alliances``, the class a game overrides (``Game(alliances=...)``).

An alliance is a formal power structure of teams. The engine keeps who is in it, in what role, and who has been
invited; the game decides what the roles start as and how an alliance is run on top of that. Roles come in three tiers:

1. **The commander**, one team, who may do every action.
2. **Subcommanders**, who may do every action except one that applies to the commander, and may fill the
   commander's seat when it is empty.
3. **Everything else**, with no rank among them: roles the alliance defines for itself (the ``edit_roles`` action), each
   a set of actions, like roles in an access list. A new alliance starts with the game's ``starting_roles`` (by default
   a treasurer, a quartermaster, a diplomat, a recruiter and a member) and the default role is what a team joins as.
   A tier-3 role acts on teams of tier 3 only, and gives only tier-3 roles.

    class Guild(Alliances):
        commander_role, subcommander_role = "master", "warden"
        starting_roles = {"squire": {"vote"}, "herald": {"speak", "vote"}}
        default_role = "squire"

``allowed`` is the one place a game can say otherwise. This is a public interface (the license exception covers it): the
names and signatures are what games build on.
"""

from ..economy import Economy, TeamGold
from . import roles

#: What a member can do for its alliance: ``invite`` teams, ``withdraw`` an invitation, ``remove`` a team, ``appoint``
#: a team to a role, ``revoke`` a team's role (it returns to the default role), ``hand_over`` the commander's seat,
#: ``disband`` the alliance, ``speak`` for it in relationships, ``open_ballot`` and ``close_ballot`` (put a question to a
#: vote and close it early), ``vote`` in ballots, and ``edit_roles`` (define, change and remove roles of tier 3). The
#: last six: ``deposit`` and ``spend`` the alliance's gold (``alliances.treasury``), ``set_fight_tax`` and ``set_sales_tax``,
#: handling its ``inventory``, and signing a ``contract`` for it; the last four belong to what comes next and nothing uses them yet.
#:
#: ``set_rules`` sets which actions need a vote. Three more are the members' own, each always by a vote: a team of tier 3
#: may put it to the members to make a team a subcommander (``elect_subcommander``) or take that away
#: (``dismiss_subcommander``), and a subcommander may put it to the subcommanders to remove the commander (``remove_commander``).
ACTIONS = (
    "invite", "withdraw", "remove", "appoint", "revoke", "hand_over", "disband", "speak", "open_ballot", "close_ballot", "vote", "edit_roles",
    "deposit", "spend", "set_fight_tax", "set_sales_tax", "inventory", "contract",
    "set_rules", "elect_subcommander", "dismiss_subcommander", "remove_commander",
)
#: What the alliance can set a voting requirement on: the actions that change the alliance, not voting itself.
GOVERNED = (
    "invite", "withdraw", "remove", "appoint", "revoke", "hand_over", "disband", "edit_roles", "set_rules",
    "deposit", "spend", "set_fight_tax", "set_sales_tax", "inventory", "contract",
)
MEMBERS_VOTE = ("elect_subcommander", "dismiss_subcommander", "remove_commander")

COMMANDER, SUBCOMMANDER, OTHER = 1, 2, 3  # the tiers


class Alliances:
    commander_role: str = "commander"
    subcommander_role: str = "subcommander"
    #: The role of a team that joins; every alliance keeps it.
    default_role: str = "member"
    #: The tier-3 roles a new alliance starts with, and what each may do. Must hold ``default_role``.
    starting_roles: dict[str, set[str]] = {
        "treasurer": {"deposit", "spend", "set_fight_tax", "set_sales_tax", "vote"},
        "quartermaster": {"inventory", "vote"},
        "diplomat": {"contract", "speak", "vote"},
        "recruiter": {"invite", "withdraw", "vote"},
        "member": {"deposit", "vote"},
    }
    #: Where the gold of a deposit comes from and goes to; ``Game`` sets it to the game's ``economy``.
    economy: Economy = TeamGold()
    #: When an alliance dissolves its treasury is split in shares (``alliances.treasury``): a team that played within the last
    #: ``share_month`` seconds has ``max_shares``, one share fewer for each further month since it last played, and none after
    #: ``max_shares`` months. Teams that left the alliance still share.
    share_month: int = 30 * 24 * 3600
    max_shares: int = 12
    #: How many teams an alliance holds, and how many alliances a team may be in.
    max_members: int = 20
    max_per_team: int = 3
    #: How many roles of tier 3 an alliance may define.
    max_roles: int = 20
    #: Voting on actions (wall clock, seconds): a team is *active* if it was selected to play within ``active_within``; a ballot
    #: that carries an action stays open ``action_ballot_seconds`` unless it passes first; an action put to a vote with no rule
    #: of the alliance's own needs ``voluntary_percent`` of the active teams (at least that many, here and everywhere); the members' vote for or against a subcommander
    #: needs ``subcommander_percent``, and a subcommander so elected is safe from the commander for ``subcommander_protection``.
    active_within: int = 7 * 24 * 3600
    action_ballot_seconds: int = 72 * 3600
    voluntary_percent: int = 50
    subcommander_percent: int = 60
    subcommander_protection: int = 7 * 24 * 3600
    #: Ballots (``alliances.ballots``): how many may be open at once, how many options one has, and whether a ballot
    #: closes as soon as everyone who may vote has.
    max_open_ballots: int = 5
    option_limits: tuple[int, int] = (2, 10)
    close_when_all_voted: bool = True

    def tier(self, role: str) -> int:
        """1 for the commander, 2 for a subcommander, 3 for any other role."""
        if role == self.commander_role:
            return COMMANDER
        return SUBCOMMANDER if role == self.subcommander_role else OTHER

    async def may_found(self, session, team) -> bool:
        """Whether ``team`` may found an alliance. Yes by default."""
        return True

    async def may_join(self, session, alliance, team) -> bool:
        """Whether ``team`` may join ``alliance`` (it has been invited). Yes by default."""
        return True

    async def allowed(self, session, alliance, actor, action: str, target=None, role: str | None = None) -> bool:
        """Whether the member ``actor`` may do ``action`` (``target`` is the member it acts on, ``role`` the role it gives).
        By default the tiers say so (see the top of this module); no one acts on itself."""
        if action not in ACTIONS or target is not None and target.id == actor.id:
            return False
        tier = self.tier(actor.role)
        if action in MEMBERS_VOTE:
            if action == "remove_commander":
                return tier == SUBCOMMANDER and (target is None or self.tier(target.role) == COMMANDER)
            wanted = OTHER if action == "elect_subcommander" else SUBCOMMANDER
            return tier == OTHER and (target is None or self.tier(target.role) == wanted)
        if tier == OTHER:
            if action not in await roles.actions(session, alliance.id, actor.role):
                return False
            return (target is None or self.tier(target.role) == OTHER) and (role is None or self.tier(role) == OTHER)
        if action == "hand_over":
            return tier == COMMANDER
        if tier == SUBCOMMANDER and target is not None and self.tier(target.role) == COMMANDER:
            return False
        if role == self.commander_role:  # the seat is handed over, or filled by a subcommander when it is empty
            return tier == SUBCOMMANDER and action == "appoint" and await roles.seat_empty(session, alliance.id, self.commander_role)
        return True

    # --- ballots ---------------------------------------------------------------------------------------------------

    async def vote_weight(self, session, alliance, member) -> int:
        """How many votes a member team casts. One each by default; a game can weigh by role or size. 0 may not vote."""
        return 1

    async def decide(self, session, alliance, ballot, totals: list[int], eligible: int) -> int | None:
        """The winning option (its index) of a ballot that has closed, or None for no decision. ``totals`` is the weight
        each option got and ``eligible`` the total weight of those who could vote (the ballot's stored result counts the teams that could instead). By default the option with the most
        weight wins (a tie, or no votes, decides nothing); a game can ask for a majority, a quorum or two thirds."""
        top = max(totals, default=0)
        if top == 0 or totals.count(top) > 1:
            return None
        return totals.index(top)

    async def on_ballot_closed(self, session, alliance, ballot, result: dict) -> None:
        """Called when a ballot closes, with its ``result`` (``winner``, ``totals``, ``turnout``, ``eligible``): the place for a game to
        act on it (remove a team, change a role, start a war) from the ballot's ``kind`` and ``payload``. Nothing by default."""

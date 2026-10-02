"""The game's say in alliances: ``Alliances``, the class a game overrides (``Game(alliances=...)``).

An alliance is a formal power structure of teams. The engine keeps who is in it, in what role, and who has been
invited; the game decides what the roles are and what each may do, and how an alliance is run on top of that (the
voting tooling comes in its own piece). The defaults are three roles, ``leader`` (everything), ``officer`` (invite,
withdraw an invitation, speak for the alliance in its relationships) and ``member``:

    class Guild(Alliances):
        roles = ("master", "warden", "knight", "squire")
        founder_role, default_role = "master", "squire"
        permissions = {"master": set(ACTIONS), "warden": {"invite", "remove", "speak"}, "knight": {"speak"}, "squire": set()}

``roles`` runs from the highest rank to the lowest. A team may act on a team of a lower rank only (``remove``,
``set_role``), and ``allowed`` is the one place a game can say otherwise. This is a public interface (the license
exception covers it): the names and signatures are what games build on.
"""

#: What a member can do for its alliance: ``invite`` teams, ``withdraw`` an invitation, ``remove`` a team, ``set_role``
#: of a team, ``hand_over`` the founder role, ``disband`` the alliance, ``speak`` for it in relationships, ``open_ballot``
#: and ``close_ballot`` (put a question to a vote and close it early), and ``vote`` in ballots.
ACTIONS = ("invite", "withdraw", "remove", "set_role", "hand_over", "disband", "speak", "open_ballot", "close_ballot", "vote")


class Alliances:
    roles: tuple[str, ...] = ("leader", "officer", "member")
    #: The role of the team that founds an alliance, and of a team that joins.
    founder_role: str = "leader"
    default_role: str = "member"
    #: What each role may do.
    permissions: dict[str, set[str]] = {
        "leader": set(ACTIONS),
        "officer": {"invite", "withdraw", "speak", "open_ballot", "vote"},
        "member": {"vote"},
    }
    #: How many teams an alliance holds, and how many alliances a team may be in.
    max_members: int = 20
    max_per_team: int = 3
    #: Ballots (``alliances.ballots``): how many may be open at once, how many options one has, and whether a ballot
    #: closes as soon as everyone who may vote has.
    max_open_ballots: int = 5
    option_limits: tuple[int, int] = (2, 10)
    close_when_all_voted: bool = True

    def rank(self, role: str) -> int:
        """0 is the highest role."""
        return self.roles.index(role)

    async def may_found(self, session, team) -> bool:
        """Whether ``team`` may found an alliance. Yes by default."""
        return True

    async def may_join(self, session, alliance, team) -> bool:
        """Whether ``team`` may join ``alliance`` (it has been invited). Yes by default."""
        return True

    async def allowed(self, session, alliance, actor, action: str, target=None, role: str | None = None) -> bool:
        """Whether the member ``actor`` may do ``action`` (``target`` is the member it acts on, ``role`` the role it gives).
        By default the role's ``permissions`` say, and a team may act only on a team of a lower rank, and give only a
        role lower than its own."""
        if action not in self.permissions.get(actor.role, ()):
            return False
        if target is not None and not self.rank(actor.role) < self.rank(target.role):
            return False
        if role is not None and role not in self.roles:
            return False
        if role is not None and not self.rank(actor.role) < self.rank(role):
            return False
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

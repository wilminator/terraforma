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
#: of a team, ``hand_over`` the founder role, ``disband`` the alliance, and ``speak`` for it in relationships.
ACTIONS = ("invite", "withdraw", "remove", "set_role", "hand_over", "disband", "speak")


class Alliances:
    roles: tuple[str, ...] = ("leader", "officer", "member")
    #: The role of the team that founds an alliance, and of a team that joins.
    founder_role: str = "leader"
    default_role: str = "member"
    #: What each role may do.
    permissions: dict[str, set[str]] = {
        "leader": set(ACTIONS),
        "officer": {"invite", "withdraw", "speak"},
        "member": set(),
    }
    #: How many teams an alliance holds, and how many alliances a team may be in.
    max_members: int = 20
    max_per_team: int = 3

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

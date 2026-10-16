"""What a game decides about contracts: which terms exist, what each is worth, how it is judged, and the numbers around them. A game
subclasses ``Contracts`` and hands it to ``Game(contracts=...)``. This is a public interface (the license exception covers it): the
method names and signatures are what games build on.

A term is a ``kind`` (a word) and its ``params`` (a dict): "don't attack the other side", "kill this monster for my quest", "deliver
this to that party". The engine ships the contract itself (offers, escrow, judging, payout) and each kind of term is added by the
game, or by a later engine piece, through this hook:

    class Pacts(terraforma.contracts.hooks.Contracts):
        def kinds(self):
            return {"no_attack"}

        def value(self, kind, params):
            return 500

        async def check(self, session, contract, term, ended):
            if await attacked(session, contract, term):
                return BROKEN
            return MET if ended else PENDING
"""

from sqlalchemy.ext.asyncio import AsyncSession

from .models import PENDING


class Contracts:
    def kinds(self) -> set[str]:
        """The kinds of term a contract may hold. None, by default: the game (or a later engine piece) adds them."""
        return set()

    def validate(self, kind: str, params: dict) -> str | None:
        """Why $params are no good for a term of this $kind, or None if they are."""
        return None

    def value(self, kind: str, params: dict) -> int:
        """What a term is worth in gold: what the other side loses if it is broken. Nothing, by default."""
        return 0

    async def check(self, session: AsyncSession, contract, term, ended: bool) -> str:
        """Whether the bound side has kept $term: ``met``, ``broken`` or ``pending`` (``models.MET``, ``BROKEN``, ``PENDING``). $ended is
        true once the contract's time is up; a term still pending then counts as broken. Pending, by default."""
        return PENDING

    def minimum_percent(self, guild: str) -> int:
        """How much of the other side's total at stake (the worth of their terms plus their escrow) a side's escrow must be at least, in
        percent. Half, by default. Below 100, so that both sides' minimums can be met at once."""
        return 50

    def base_fee(self, guild: str) -> int:
        """The base gold each side pays the guild to be party to a contract, before the game's fee rule (``Game(fees=...)``). 10, by
        default."""
        return 10

    def offer_lifetime(self, guild: str) -> int:
        """How many seconds the other side has to answer an offer. Three days, by default."""
        return 3 * 24 * 3600

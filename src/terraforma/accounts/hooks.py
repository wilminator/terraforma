"""What a game decides about signing in. A game subclasses ``SignIn`` and hands it to ``Game(sign_in=...)``. This is a public
interface (the license exception covers it).

    class SignIn(terraforma.accounts.hooks.SignIn):
        remember_browser_days = 14
        def single_method_note(self, account, methods):
            return "Add a second way in on your account page: a lost phone should not lock you out."
"""

from ..models import Account


class SignIn:
    #: How many days a browser that proved a second factor may skip it (0: browsers are never remembered). The player is
    #: asked at login ("remember this browser"); the engine's own default is a month.
    remember_browser_days: int = 30

    def single_method_note(self, account: Account, methods: list[str]) -> str | None:
        """A note shown after login to a player whose two-factor login rests on a single method ($methods is "app" and/or
        "key"; recovery codes are not a method): a lost phone or key then leaves only the recovery codes. None: no note."""
        if len(methods) != 1:
            return None
        return (
            "You have only one second factor. If you lose it, only your recovery codes get you back in, "
            "so add a second one (an authenticator app or a security key) on your account page."
        )

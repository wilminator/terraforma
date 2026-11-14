"""What another player sees in place of a name that failed the screen (``screen``): nothing is ever refused or rewritten, so the
hiding happens here, when a name is shown. The owner always sees their own name; everybody else sees ``name`` unless somebody called
it unsafe (``name_ok`` is False). A name that was never checked (None) is shown: nobody has said it is unsafe."""

HERO = "Hero"
TEAM = "Team"
ALLIANCE = "Alliance"
PLAYER = "Player"


def public_name(name: str, name_ok: bool | None, fallback: str, *, own: bool = False) -> str:
    """The name to show a viewer: $name for its owner ($own) or when nobody called it unsafe (``name_ok`` is not False), else $fallback."""
    return name if own or name_ok is not False else fallback


def seen_by(name: str | None, name_ok: bool | None, fallback: str, owner_id: int | None, viewer_id: int | None) -> str | None:
    """``public_name`` for a name whose owner is the account $owner_id, read by the account $viewer_id (None: nobody is signed in), which
    passes None through (a name that is not there stays not there)."""
    if name is None:
        return None
    return public_name(name, name_ok, fallback, own=viewer_id is not None and viewer_id == owner_id)

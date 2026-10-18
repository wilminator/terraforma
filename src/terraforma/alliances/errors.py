"""What the alliance calls refuse with: a message the player can read."""


class AllianceError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(AllianceError):
    pass


class Forbidden(AllianceError):
    """The team's role in the alliance, or the game's rules, do not let it."""

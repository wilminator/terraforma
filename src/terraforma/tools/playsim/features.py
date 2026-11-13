"""What slice 5 of the living world has landed. Each pending scenario in ``scenarios`` asks here; while the answer is no it runs as skipped.

When a slice 5 piece merges, make its probe say what it added (a hook on ``Towns``, a module, a route) and write the scenario body:
the plan of what it must check is the scenario's ``PENDING`` entry.
"""

#: What each found piece is called, for the skipped scenario to say (filled in by ``found``).
FOUND: dict[str, str] = {}


def found(name: str, sim) -> bool:
    """Whether the piece the pending scenario $name waits for exists in this engine and game."""
    probe = PROBES.get(name)
    answer = probe(sim) if probe else None
    if answer:
        FOUND[name] = answer
    return bool(answer)


#: pending scenario -> function(sim) returning a short description of the piece when it exists, else "".
#: Flip these as the slices land, e.g. ``lambda sim: "Towns.is_open" if hasattr(sim.game.towns, "is_open") else ""``.
PROBES: dict = {
    "ruins-closed": lambda sim: "",
    "world-events": lambda sim: "",
    "strength-from-play": lambda sim: "",
    "dormant-villages": lambda sim: "",
}

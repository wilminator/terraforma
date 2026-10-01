"""A game's seed data: its starting content, as JSON files in git.

One file per kind of content (``abilities.json``, ``items.json``,
``jobs.json``, ``monsters.json``, ``personalities.json``), each a list of
objects. This only reads the files; content.schema checks them against the
formats in the README and content.loader loads them into the database.
"""

import json
from pathlib import Path


class SeedError(ValueError):
    pass


def load_seed(seed_dir: Path) -> dict[str, list[dict]]:
    """Every seed file in $seed_dir, by name (``monsters.json`` -> ``"monsters"``)."""
    seed = {}
    for path in sorted(Path(seed_dir).glob("*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SeedError(f"{path.name}: not valid JSON ({error})") from error
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SeedError(f"{path.name}: must be a list of objects")
        seed[path.stem] = rows
    return seed

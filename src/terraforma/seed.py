"""A game's seed data: its starting content, as JSON files in git.

One file per kind of content (``abilities.json``, ``items.json``,
``jobs.json``, ``monsters.json``, ``personalities.json``), each a list of
objects, or a folder of such files (``monsters/``) when one file grows too big. This only reads the files; content.schema checks them against the
formats in the README and content.loader loads them into the database.
"""

import json
from pathlib import Path


class SeedError(ValueError):
    pass


def load_seed(seed_dir: Path) -> dict[str, list[dict]]:
    """Every kind of content in $seed_dir, by name (``monsters.json`` and the files of ``monsters/`` -> ``"monsters"``).

    A kind may be one file, a folder of files (any depth, ``.json`` only, hidden names skipped), or both: ``monsters.json`` first,
    then the folder's files in path order. Rows are joined in that order. A ``key`` used in two files of one kind is refused,
    naming both files (a repeat inside one file is for ``check_seed``, which also names the row)."""
    seed_dir = Path(seed_dir)
    files: dict[str, list[Path]] = {}
    for path in sorted(seed_dir.glob("*.json")):
        files.setdefault(path.stem, []).append(path)
    for folder in sorted(seed_dir.iterdir()):
        if folder.is_dir() and not folder.name.startswith("."):
            found = [path for path in sorted(folder.rglob("*.json")) if not any(part.startswith(".") for part in path.relative_to(folder).parts)]
            if found:
                files.setdefault(folder.name, []).extend(found)
    seed = {}
    for name, paths in sorted(files.items()):
        rows: list[dict] = []
        where: dict[str, str] = {}
        for path in paths:
            label = path.relative_to(seed_dir).as_posix()
            for row in read_rows(path, label):
                key = row.get("key")
                if isinstance(key, str):
                    if key in where and where[key] != label:
                        raise SeedError(f"{name}: the key {key!r} is in {where[key]} and in {label}")
                    where.setdefault(key, label)
                rows.append(row)
        seed[name] = rows
    return seed


def read_rows(path: Path, label: str) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SeedError(f"{label}: not valid JSON ({error})") from error
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SeedError(f"{label}: must be a list of objects")
    return rows

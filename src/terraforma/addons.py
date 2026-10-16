"""Add-ons: packages that add content to a game without being a game (see README, "Add-ons").

An add-on is a seed folder and an assets folder, like a game's. The game lists the add-ons it supports (``Game(addons=...)``); the
operator lists the ones to use in ``settings.toml`` (``addons = ["monster_pack"]``). The engine never imports an add-on: the game does.
Listing an add-on adds its seed rows and its assets to the game's, as layers: the game's first, then the add-ons in the order listed.

    seed:    a key (``monsters.json`` row ``key``) used in two layers is refused, naming both.
    assets:  a file name used in two layers is refused, naming both.
    license: an add-on's assets folder needs an ``ASSETS.md`` that records the source and license of every file in it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .animations import AnimationError, check_animation_folder
from .colormaps import ColorMapError, check_color_map_folder
from .seed import SeedError, load_seed

ADDON_KEY = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
LICENSE_FILE = "ASSETS.md"

#: The kinds of seed file an add-on may have: the ones whose rows can be switched off (they have ``active``; content.loader.TABLES).
#: Components start off, so an add-on's maps, dialogs, NPCs and quests wait for a slice that can switch them.
SWITCHABLE = ("statuses", "abilities", "items", "drop_tables", "personalities", "jobs", "monsters")


class AddOnError(ValueError):
    """The add-ons are not as the game and the settings say: the server won't start on them."""


@dataclass(frozen=True)
class AddOn:
    #: Names the add-on in ``settings.toml`` and, later, in the database: lowercase letters, digits and ``_``, 2 to 32 characters.
    key: str
    #: What an admin reads.
    name: str
    #: Its seed data (JSON, in the same formats as a game's) and assets, when it has them.
    seed_dir: Path | None = None
    assets_dir: Path | None = None


@dataclass(frozen=True)
class LayeredSeed:
    """The game's seed with the listed add-ons' rows added, and who each row came from."""

    #: Like ``load_seed``: the rows of each file, the game's first and then each add-on's, in the order listed.
    rows: dict[str, list[dict]]
    #: For each file and row key, the key of the add-on that brought it (None: the game's own).
    owners: dict[str, dict[str, str | None]]


@dataclass(frozen=True)
class Integrated:
    """The game with the add-ons the operator listed: what the app serves and loads."""

    #: The listed add-ons, in the order listed.
    addons: tuple[AddOn, ...]
    #: Every assets folder, the game's first and then each add-on's: a file is served from the first folder that has it.
    asset_dirs: tuple[Path, ...]
    #: Every seed folder with the key of its add-on (None: the game's), in the order they are layered.
    seed_dirs: tuple[tuple[str | None, Path], ...]

    def load_seed(self) -> LayeredSeed:
        return layer_seeds(self.seed_dirs)


def integrate(game, listed: list[str] | tuple[str, ...]) -> Integrated:
    """$game with the add-ons named in $listed (the settings' ``addons``); raises AddOnError naming what is wrong.

    Checks everything that needs only files: that the game's add-ons are well formed, that each listed key is one of them, and that
    the assets folders neither collide nor lack a license record. (The seed is read, and its keys checked, by ``Integrated.load_seed``
    when the server starts.)"""
    supported = {}
    for addon in game.addons if game else ():
        if not ADDON_KEY.fullmatch(addon.key):
            raise AddOnError(f"add-on {addon.key!r}: the key must be 2 to 32 lowercase letters, digits or _, starting with a letter")
        if addon.key in supported:
            raise AddOnError(f"the game lists the add-on {addon.key!r} twice")
        supported[addon.key] = addon
    if len(set(listed)) != len(listed):
        raise AddOnError("settings: an add-on is listed twice")
    chosen = []
    for key in listed:
        if key not in supported:
            known = ", ".join(sorted(supported)) or "none"
            raise AddOnError(f"settings: the add-on {key!r} is not one this game supports (it supports: {known})")
        chosen.append(supported[key])
    for addon in chosen:
        for field in ("seed_dir", "assets_dir"):
            folder = getattr(addon, field)
            if folder is not None and not Path(folder).is_dir():
                raise AddOnError(f"add-on {addon.key}: {field} {folder} is not a folder")
        if addon.assets_dir is not None:
            check_assets_folder(addon)
    asset_dirs = [(None, game.assets_dir)] if game and game.assets_dir is not None else []
    asset_dirs += [(addon.key, addon.assets_dir) for addon in chosen if addon.assets_dir is not None]
    check_asset_names(asset_dirs)
    seed_dirs = [(None, game.seed_dir)] if game and game.seed_dir is not None else []
    seed_dirs += [(addon.key, addon.seed_dir) for addon in chosen if addon.seed_dir is not None]
    return Integrated(tuple(chosen), tuple(Path(folder) for _, folder in asset_dirs), tuple(seed_dirs))


# --- the seed ------------------------------------------------------------------------------------------------------

def layer_seeds(layers: tuple[tuple[str | None, Path], ...]) -> LayeredSeed:
    """The seeds of $layers (an add-on key or None for the game, and its seed folder) joined; raises AddOnError if a key is in two."""
    rows: dict[str, list[dict]] = {}
    owners: dict[str, dict[str, str | None]] = {}
    for owner, folder in layers:
        try:
            seed = load_seed(folder)
        except SeedError as error:
            raise AddOnError(f"{owner or 'the game'}'s seed: {error}") from error
        for name, entries in seed.items():
            if owner is not None and name not in SWITCHABLE:
                raise AddOnError(f"the add-on {owner}: {name}.json cannot be used yet (an add-on's seed may have: {', '.join(SWITCHABLE)})")
            known = owners.setdefault(name, {})
            for entry in entries:
                key = entry.get("key")
                if isinstance(key, str):
                    if key in known:
                        raise AddOnError(f"{name}.json: the key {key!r} is in {_who(known[key])} and in {_who(owner)}; an add-on cannot reuse a key")
                    known[key] = owner
            rows.setdefault(name, []).extend(entries)
    return LayeredSeed(rows, owners)


def _who(owner: str | None) -> str:
    return f"the add-on {owner}" if owner else "the game"


# --- the assets ----------------------------------------------------------------------------------------------------

def asset_files(folder: Path) -> list[str]:
    """The files under $folder as paths with ``/``, not counting the license record or anything hidden."""
    found = []
    for path in sorted(Path(folder).rglob("*")):
        relative = path.relative_to(folder)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts) and relative.as_posix() != LICENSE_FILE:
            found.append(relative.as_posix())
    return found


def check_asset_names(layers: list[tuple[str | None, Path]]) -> None:
    """Raises AddOnError if the same file name is in two of $layers (an add-on key or None for the game, and its assets folder)."""
    seen: dict[str, str | None] = {}
    for owner, folder in layers:
        for name in asset_files(folder):
            if name in seen:
                raise AddOnError(f"assets: the file {name!r} is in {_who(seen[name])} and in {_who(owner)}; an add-on cannot reuse a file name")
            seen[name] = owner


def check_assets_folder(addon: AddOn) -> None:
    """Raises AddOnError unless $addon's assets are well formed and every file is recorded in its ``ASSETS.md`` with a usable license."""
    folder = Path(addon.assets_dir)
    for check, error_type in ((check_animation_folder, AnimationError), (check_color_map_folder, ColorMapError)):
        try:
            check(folder)
        except error_type as error:
            raise AddOnError(f"add-on {addon.key}: assets: {error}") from error
    files = asset_files(folder)
    record = folder / LICENSE_FILE
    if not record.is_file():
        if files:
            raise AddOnError(f"add-on {addon.key}: assets_dir needs an {LICENSE_FILE} recording the source and license of each file")
        return
    recorded: set[str] = set()
    problems = []
    for line in record.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        names = re.findall(r"`([^`]+)`", cells[0]) if cells else []
        if not names:
            continue  # the header, the rule under it, or text
        if len(cells) < 3:
            problems.append(f"the row for {names[0]} needs a source and a license")
            continue
        if not cells[1]:
            problems.append(f"the row for {names[0]} has no source")
        why = license_problem(cells[2])
        if why:
            problems.append(f"the row for {names[0]}: {why}")
        recorded.update(names)
    problems += [f"{name} is not recorded" for name in files if name not in recorded]
    if problems:
        raise AddOnError(f"add-on {addon.key}: {LICENSE_FILE} has problems:\n  " + "\n  ".join(problems))


UNUSABLE = re.compile(r"\b(unknown|tbd|todo|unlicensed|proprietary|all rights reserved|personal use|non-?commercial|no[- ]?derivs?|no[- ]?derivatives?)\b", re.IGNORECASE)
CREATIVE_COMMONS = re.compile(r"\bCC[ -]?BY((?:[- ](?:NC|ND|SA))*)(?:[ -]?(\d(?:\.\d)?))?", re.IGNORECASE)


def license_problem(text: str) -> str | None:
    """Why $text (the license cell of a record) is not a license a public AGPL repo can carry, or None if it may be.

    A license has to be named and compatible with the AGPL: any, but never an unknown one, non-commercial, no-derivatives, or
    CC-BY-SA before version 4.0. This reads only what the cell says; whether it is true is for the person who adds the asset."""
    if not text.strip():
        return "no license"
    if match := UNUSABLE.search(text):
        return f"{match.group(0)!r} is not a license the engine's repository can carry"
    for match in CREATIVE_COMMONS.finditer(text):
        terms = match.group(1).upper()
        if "NC" in terms or "ND" in terms:
            return f"{match.group(0)!r} is not usable (no non-commercial or no-derivatives licenses)"
        if "SA" in terms and not (match.group(2) and float(match.group(2)) >= 4):
            return f"{match.group(0)!r} is not usable (CC-BY-SA only from version 4.0)"
    return None


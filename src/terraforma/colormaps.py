"""The color map: ``name.colors.json`` under the game's assets folder, a recolor of a tilesheet (``{"#3050a0": "#a03030"}``).

A public format (the license exception covers it), described in the README under Art. The browser applies it
(``client/static/lib/js/art.js``): each pixel is snapped to the nearest color of the sheet's palette, then swapped if the map lists that
color. The server never applies a map; it only checks the files when it starts, so a typo stops the server, not a player's page. The
tool ``terraforma.tools.colormap`` makes, checks, joins and previews maps.
"""

import json
import re
from pathlib import Path

HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")  # what the browser's parseHex accepts


class ColorMapError(ValueError):
    """Says what is wrong with a color map."""


def normalize(text: object) -> str:
    """``#3050A0`` or ``3050a0`` as ``#3050a0``; raises ColorMapError for anything else."""
    found = HEX.fullmatch(text.strip()) if isinstance(text, str) else None
    if not found:
        raise ColorMapError(f"{text!r} is not a color like #3050a0")
    return "#" + found[1].lower()


def rgb(color: str) -> tuple[int, int, int]:
    value = int(normalize(color)[1:], 16)
    return value >> 16, (value >> 8) & 255, value & 255


def hexed(red: int, green: int, blue: int) -> str:
    return f"#{red:02x}{green:02x}{blue:02x}"


def check_color_map(data: object) -> dict[str, str]:
    """The map $data with every color written ``#rrggbb``; raises ColorMapError if it is not an object of colors to colors, or lists a color twice
    (``#3050A0`` and ``#3050a0`` are one color)."""
    if not isinstance(data, dict):
        raise ColorMapError('a color map is an object, like {"#3050a0": "#a03030"}')
    checked: dict[str, str] = {}
    for key, value in data.items():
        old = normalize(key)
        if old in checked:
            raise ColorMapError(f"{old} is listed twice")
        checked[old] = normalize(value)
    return checked


def check_color_map_folder(folder: Path) -> None:
    """Every ``*.colors.json`` in $folder checked; raises ColorMapError naming the file."""
    for path in sorted(Path(folder).glob("*.colors.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ColorMapError(f"{path.name}: not JSON ({error})") from error
        try:
            check_color_map(data)
        except ColorMapError as error:
            raise ColorMapError(f"{path.name}: {error}") from error


def nearest(color: tuple[int, int, int], palette: list[tuple[int, int, int]]) -> int:
    """The index of the palette color nearest $color (squared distance in RGB; the first wins a tie, as in the browser)."""
    return min(range(len(palette)), key=lambda n: (sum((a - b) ** 2 for a, b in zip(color, palette[n])), n))


def apply(color: tuple[int, int, int], palette: list[tuple[int, int, int]], swaps: dict[tuple[int, int, int], tuple[int, int, int]]):
    """What the browser draws for a pixel of $color: snapped to $palette, then swapped if $swaps lists it."""
    snapped = palette[nearest(color, palette)] if palette else color
    return swaps.get(snapped, color)

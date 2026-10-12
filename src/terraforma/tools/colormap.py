"""Makes, checks, joins and previews color maps (``name.colors.json``), the recolors of a tilesheet.

    python -m terraforma.tools.colormap colors out/knight                         # the sheet's palette
    python -m terraforma.tools.colormap make out/knight out/red_team --hue 150     # a map that turns every color 150 degrees round the wheel
    python -m terraforma.tools.colormap make out/knight out/gold --only "#3050a0,#6080d0" --swap "#102040=#403010"
    python -m terraforma.tools.colormap check out/red_team.colors.json --sheet out/knight
    python -m terraforma.tools.colormap merge out/red_team out/dark out/dark_red   # red_team first, then dark
    python -m terraforma.tools.colormap preview out/knight out/red_team out/look.png --scale 4

A SHEET is written without its extension (``out/knight``): the tool reads ``out/knight.sheet.json`` (and, for ``preview``, the pictures). A map
is written the same way, or with ``.colors.json``. ``make`` changes the colors of the sheet's palette by ``--hue`` (degrees), ``--saturation`` and
``--lightness`` (factors; 1 keeps) and by ``--swap FROM=TO`` (which wins over the others); ``--only`` and ``--skip`` choose which colors the
first three touch. The map lists only the colors that change. ``check`` checks the format and, with ``--sheet``, that every color the map
changes is one of the sheet's palette (a color the palette lacks never matches, so such an entry does nothing). ``merge`` writes the map that does
what the first does and then the second (``--sheet`` keeps it to the sheet's colors). ``preview`` draws the sheet recolored the way the browser does (it needs Pillow).

Everything but ``preview`` uses the standard library only; ``preview`` needs Pillow (``pip install "terraforma[tools]"``).
"""

import argparse
import colorsys
import json
import sys
from pathlib import Path

from terraforma.colormaps import ColorMapError, apply, check_color_map, hexed, normalize, rgb

SUFFIXES = (".colors.json", ".sheet.json", ".alpha.png", ".png", ".json")


class ColorMapToolError(ValueError):
    """The sheet, the map or an option cannot do what was asked."""


def _stem(name: Path, suffixes: tuple[str, ...] = SUFFIXES) -> str:
    text = str(name)
    for suffix in suffixes:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def palette_of(sheet: Path) -> list[str]:
    """The palette (``#rrggbb``, in the sheet's order) of ``SHEET.sheet.json``."""
    path = Path(_stem(sheet) + ".sheet.json")
    try:
        palette = json.loads(path.read_text(encoding="utf-8"))["palette"]
        return [normalize(color) for color in palette]
    except FileNotFoundError as error:
        raise ColorMapToolError(f"{path}: no such file") from error
    except (OSError, ValueError, KeyError, TypeError, ColorMapError) as error:
        raise ColorMapToolError(f"{path}: not a sheet file with a palette ({error})") from error


def read_map(path: Path) -> dict[str, str]:
    """The map in ``MAP.colors.json`` (``MAP`` may carry the extension), checked."""
    file = Path(_stem(path, (".colors.json",)) + ".colors.json")
    try:
        return check_color_map(json.loads(file.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise ColorMapToolError(f"{file}: no such file") from error
    except (OSError, ValueError) as error:
        raise ColorMapToolError(f"{file}: {error}") from error


def write_map(path: Path, colors: dict[str, str]) -> Path:
    file = Path(_stem(path, (".colors.json",)) + ".colors.json")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(colors, indent=1) + "\n", encoding="utf-8")
    return file


def _shift(color: str, hue: float, saturation: float, lightness: float) -> str:
    """$color turned $hue degrees round the wheel, its saturation and lightness multiplied (HLS, kept within 0 to 1)."""
    red, green, blue = (value / 255 for value in rgb(color))
    h, l, s = colorsys.rgb_to_hls(red, green, blue)
    h = (h + hue / 360) % 1
    l = min(1.0, max(0.0, l * lightness))
    s = min(1.0, max(0.0, s * saturation))
    return hexed(*(round(value * 255) for value in colorsys.hls_to_rgb(h, l, s)))


def make(
    palette: list[str],
    hue: float = 0,
    saturation: float = 1,
    lightness: float = 1,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    swaps: dict[str, str] | None = None,
) -> dict[str, str]:
    """The map for a sheet of $palette: each color of $only (all, if None) but those of $skip is shifted; $swaps then set their colors outright.
    Only the colors that change are listed, in the palette's order."""
    if saturation < 0 or lightness < 0:
        raise ColorMapToolError("--saturation and --lightness are factors, 0 or more")
    chosen = {normalize(color) for color in only} if only is not None else set(palette)
    chosen -= {normalize(color) for color in skip or []}
    swaps = {normalize(old): normalize(new) for old, new in (swaps or {}).items()}
    for color in [*chosen, *swaps]:
        if color not in palette:
            raise ColorMapToolError(f"{color} is not in the sheet's palette ({', '.join(palette)})")
    result = {}
    for color in palette:
        new = swaps.get(color) or (_shift(color, hue, saturation, lightness) if color in chosen else color)
        if new != color:
            result[color] = new
    if not result:
        raise ColorMapToolError("nothing changes: that map would be empty")
    return result


def check(colors: dict[str, str], palette: list[str] | None = None) -> list[str]:
    """What is wrong with the checked map $colors for a sheet of $palette (None: not known); an empty list when it is fine."""
    problems = []
    if palette is not None:
        problems += [f"{color} is not in the sheet's palette, so this entry does nothing" for color in colors if color not in palette]
    return problems


def merge(first: dict[str, str], second: dict[str, str], palette: list[str] | None = None) -> dict[str, str]:
    """The map that does what $first does and then $second: a color $first turns into one $second lists goes on to what $second says.
    $second's entries for colors $first does not change are kept; given the sheet's $palette, only those of its colors are (an entry for the
    colors $first makes is then not carried over, as no pixel is ever that color before the map)."""
    joined = {old: second.get(new, new) for old, new in first.items()}
    joined.update({old: new for old, new in second.items() if old not in first and (palette is None or old in palette)})
    return {old: new for old, new in joined.items() if old != new}


def preview(sheet: Path, colors: dict[str, str], target: Path, scale: int = 1) -> None:
    """Writes a picture of $sheet recolored by $colors (as the browser draws it) at $target."""
    try:
        from PIL import Image
    except ImportError as error:
        raise ColorMapToolError('Pillow is not installed: pip install "terraforma[tools]"') from error
    if scale < 1:
        raise ColorMapToolError("--scale is a whole number, 1 or more")
    stem = _stem(sheet)
    palette = [rgb(color) for color in palette_of(sheet)]
    swaps = {rgb(old): rgb(new) for old, new in colors.items()}
    try:
        with Image.open(stem + ".png") as picture:
            image = picture.convert("RGBA")
    except (OSError, ValueError) as error:
        raise ColorMapToolError(f"{stem}.png: not a picture Pillow can read") from error
    alpha_path = Path(stem + ".alpha.png")
    if alpha_path.is_file():
        with Image.open(alpha_path) as picture:
            image.putalpha(picture.convert("L"))
    raw = image.tobytes()
    out = bytearray(len(raw))
    memo: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for at in range(0, len(raw), 4):
        pixel = (raw[at], raw[at + 1], raw[at + 2])
        if pixel not in memo:
            memo[pixel] = apply(pixel, palette, swaps)
        out[at : at + 3] = bytes(memo[pixel])
        out[at + 3] = raw[at + 3]
    drawn = Image.frombytes("RGBA", image.size, bytes(out))
    if scale > 1:
        drawn = drawn.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    target.parent.mkdir(parents=True, exist_ok=True)
    drawn.save(target)


def _swap(text: str) -> tuple[str, str]:
    old, found, new = text.partition("=")
    if not found:
        raise argparse.ArgumentTypeError(f"{text!r} is not FROM=TO, like #3050a0=#a03030")
    try:
        return normalize(old), normalize(new)
    except ColorMapError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _colors(text: str) -> list[str]:
    return [part for part in (each.strip() for each in text.split(",")) if part]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="colormap", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    each = commands.add_parser("colors", help="list a sheet's palette")
    each.add_argument("sheet", type=Path)
    each = commands.add_parser("make", help="make a map from a sheet's palette")
    each.add_argument("sheet", type=Path)
    each.add_argument("target", type=Path, help="where to write the map, without an extension")
    each.add_argument("--hue", type=float, default=0, help="degrees round the color wheel")
    each.add_argument("--saturation", type=float, default=1, help="a factor (default 1)")
    each.add_argument("--lightness", type=float, default=1, help="a factor (default 1)")
    each.add_argument("--only", type=_colors, help="comma-separated palette colors; the shifts touch only these")
    each.add_argument("--skip", type=_colors, help="comma-separated palette colors the shifts leave alone")
    each.add_argument("--swap", type=_swap, action="append", default=[], metavar="FROM=TO", help="set a color outright (repeat for more)")
    each = commands.add_parser("check", help="check maps")
    each.add_argument("maps", type=Path, nargs="+")
    each.add_argument("--sheet", type=Path, help="also check every changed color is in this sheet's palette")
    each = commands.add_parser("merge", help="join two maps: the first, then the second")
    each.add_argument("first", type=Path)
    each.add_argument("second", type=Path)
    each.add_argument("target", type=Path, help="where to write the joined map, without an extension")
    each.add_argument("--sheet", type=Path, help="the sheet the maps are for: keeps the joined map to its palette's colors")
    each = commands.add_parser("preview", help="draw a sheet recolored by a map")
    each.add_argument("sheet", type=Path)
    each.add_argument("map", type=Path)
    each.add_argument("target", type=Path, help="the picture to write (.png)")
    each.add_argument("--scale", type=int, default=1, help="enlarge by whole pixels (default 1)")
    return parser


def main(argv: list[str]) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "colors":
            print("\n".join(palette_of(arguments.sheet)))
        elif arguments.command == "make":
            colors = make(
                palette_of(arguments.sheet), arguments.hue, arguments.saturation, arguments.lightness, arguments.only, arguments.skip, dict(arguments.swap)
            )
            print(f"{write_map(arguments.target, colors)}: {len(colors)} colors change")
        elif arguments.command == "check":
            palette = palette_of(arguments.sheet) if arguments.sheet else None
            failed = False
            for path in arguments.maps:
                problems = check(read_map(path), palette)
                failed |= bool(problems)
                for problem in problems:
                    print(f"{_stem(path, ('.colors.json',))}.colors.json: {problem}", file=sys.stderr)
            if failed:
                return 1
            print(f"{len(arguments.maps)} maps fine")
        elif arguments.command == "merge":
            colors = merge(read_map(arguments.first), read_map(arguments.second), palette_of(arguments.sheet) if arguments.sheet else None)
            print(f"{write_map(arguments.target, colors)}: {len(colors)} colors change")
        else:
            preview(arguments.sheet, read_map(arguments.map), arguments.target, arguments.scale)
            print(f"wrote {arguments.target}")
    except (ColorMapToolError, ColorMapError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

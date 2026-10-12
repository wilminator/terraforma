"""Turns a picture or a GIF into a tilesheet the client draws.

    python -m terraforma.tools.tilesheet knight.png out/knight --frame 32x32 --scale 2 --colors 32
    python -m terraforma.tools.tilesheet walk.gif out/walk      # one frame for each GIF frame, plus out/walk.anim.json

A still picture (PNG, JPEG, WebP...) is cut into frames of ``--frame`` (the whole picture is one frame if left out), left to right and then
down. A GIF (or any animated picture) gives one frame for each of its frames, and the tool also writes its timing as a default animation
file (``--animation`` names it, ``base`` unless said; ``--no-animation`` writes none). ``--frames`` keeps only the first cells of a still picture
(for a sheet with empty cells at the end), ``--columns`` sets how many frames lie across the sheet, ``--scale`` resizes each frame (a
number above 1 enlarges it by whole pixels, below 1 shrinks it) and ``--colors`` is the most colors the palette may have (2 to 256).

It writes the engine's tilesheet format (see ``client/static/lib/js/art.js``): ``NAME.png`` (an indexed picture), ``NAME.alpha.png`` (the 8-bit
transparency as gray; only when some pixel is not opaque) and ``NAME.sheet.json``. The palette is made from the pixels that can be seen, so a
color hidden under a clear pixel costs none. ``--combined`` writes one RGBA picture instead of the two (the sheet then says ``alpha: false``).

Needs Pillow (``pip install "terraforma[tools]"``); the engine itself does not.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from terraforma.animations import NAME, AnimationError, check_animations

try:
    from PIL import Image, ImageSequence, UnidentifiedImageError
except ImportError:  # the tool alone needs it
    Image = None

DEFAULT_COLUMNS = 8  # across, for the frames of a GIF
DEFAULT_MS = 100  # what a browser shows a GIF frame with no time (or a very short one) for
MIN_GIF_MS = 20


class TilesheetError(ValueError):
    """The picture or an option cannot make a sheet."""


def _open(source: Path):
    """The frames of $source as RGBA pictures, and the time of each in ms (None for a still picture), and whether the picture loops."""
    if Image is None:
        raise TilesheetError('Pillow is not installed: pip install "terraforma[tools]"')
    try:
        with Image.open(source) as picture:
            picture.load()
            if getattr(picture, "n_frames", 1) <= 1:
                return [picture.convert("RGBA")], None, False
            frames, times = [], []
            for each in ImageSequence.Iterator(picture):
                frames.append(each.convert("RGBA"))
                ms = each.info.get("duration", 0) or 0
                times.append(int(ms) if ms >= MIN_GIF_MS else DEFAULT_MS)
            return frames, times, picture.info.get("loop") is not None
    except FileNotFoundError as error:
        raise TilesheetError(f"{source}: no such file") from error
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise TilesheetError(f"{source}: not a picture Pillow can read") from error


def _cut(picture, frame: tuple[int, int] | None, limit: int | None):
    """($frames, the grid of the picture): its cells of $frame (the whole picture if None), the first $limit of them."""
    width, height = picture.size
    frame_width, frame_height = frame or (width, height)
    if width % frame_width or height % frame_height:
        raise TilesheetError(f"a picture of {width} by {height} pixels is not whole frames of {frame_width} by {frame_height}")
    across, down = width // frame_width, height // frame_height
    count = across * down
    if limit is not None:
        if not 1 <= limit <= count:
            raise TilesheetError(f"--frames {limit}, but the picture holds {count}")
        count = limit
    cells = [picture.crop((n % across * frame_width, n // across * frame_height, (n % across + 1) * frame_width, (n // across + 1) * frame_height)) for n in range(count)]
    return cells, across


def _resize(cells, scale: float):
    width, height = cells[0].size
    new = (round(width * scale), round(height * scale))
    if not scale > 0 or min(new) < 1:
        raise TilesheetError(f"--scale {scale:g} leaves a frame of {new[0]} by {new[1]} pixels")
    if new == (width, height):
        return cells
    method = Image.Resampling.NEAREST if scale >= 1 else Image.Resampling.BOX
    return [cell.resize(new, method) for cell in cells]


def _rgba(atlas) -> list[tuple[int, int, int, int]]:
    """The pixels of an RGBA picture, row by row (Pillow's own ``getdata`` is deprecated)."""
    raw = atlas.tobytes()
    return list(zip(raw[0::4], raw[1::4], raw[2::4], raw[3::4]))


def _palette(atlas, colors: int):
    """The (r, g, b) colors, at most $colors, of the pixels of $atlas that can be seen: all of them if there are few enough, else a median cut."""
    seen = list(dict.fromkeys(pixel[:3] for pixel in _rgba(atlas) if pixel[3]))
    if not seen:
        raise TilesheetError("the picture has nothing to see: every pixel is clear")
    if len(seen) <= colors:
        return seen
    strip = Image.new("RGB", (len(seen), 1))
    strip.frombytes(bytes(value for color in seen for value in color))
    reduced = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    table = reduced.getpalette()
    return [tuple(table[n * 3 : n * 3 + 3]) for n in sorted(set(reduced.tobytes()))]


def _indexed(atlas, palette):
    """The atlas as an indexed picture of $palette (each pixel nearest), clear pixels at index 0, with the palette trimmed to what is used.
    Returns (the picture, the palette in its order)."""
    table = Image.new("P", (1, 1))
    flat = [value for color in palette for value in color]
    table.putpalette(flat + [0] * (768 - len(flat)))
    indexed = atlas.convert("RGB").quantize(palette=table, dither=Image.Dither.NONE)
    clear = atlas.getchannel("A").point(lambda value: 255 if value == 0 else 0)
    indexed.paste(0, mask=clear)
    used = sorted(set(indexed.tobytes()))
    indexed = indexed.remap_palette(used)
    return indexed, [palette[index] for index in used]


def _animation(name: str, times: list[int], loop: bool, frames: int) -> dict:
    """The default animation: every frame in order; the commonest time is the animation's and a frame with another says its own."""
    ms = Counter(times).most_common(1)[0][0]
    listed = [number if time == ms else {"frame": number, "ms": time} for number, time in enumerate(times)]
    data = {"animations": {name: {"frames": listed, "ms": ms, "loop": loop}}}
    try:
        check_animations(data, frames=frames)
    except AnimationError as error:
        raise TilesheetError(f"the animation does not fit an animation file (use --no-animation to skip it): {error}") from error
    return data


def convert(
    source: Path,
    target: Path,
    frame: tuple[int, int] | None = None,
    scale: float = 1,
    colors: int = 256,
    columns: int | None = None,
    frames: int | None = None,
    combined: bool = False,
    animation: str | None = "base",
) -> dict:
    """Reads $source and writes ``target.png``, ``target.alpha.png`` (when some pixel is not opaque and not $combined), ``target.sheet.json`` and,
    for an animated $source, ``target.anim.json`` named $animation (None: none). Returns the sheet's JSON as a dict. Raises TilesheetError."""
    if not 2 <= colors <= 256:
        raise TilesheetError(f"--colors {colors}: a sheet has 2 to 256 colors")
    if animation is not None and not NAME.fullmatch(animation):
        raise TilesheetError(f"--animation {animation!r}: a name is 1-64 lowercase letters, digits or _, starting with a letter")
    pictures, times, loop = _open(source)
    if times is None:
        cells, across = _cut(pictures[0], frame, frames)
    else:
        if frame is not None or frames is not None:
            raise TilesheetError("--frame and --frames are for a still picture: a GIF has its own frames")
        cells, across = pictures, DEFAULT_COLUMNS
    cells = _resize(cells, scale)
    count = len(cells)
    across = max(1, min(columns or across, count))
    down = -(-count // across)
    width, height = cells[0].size
    animation_file = _animation(animation, times, loop, count) if times is not None and animation is not None else None

    atlas = Image.new("RGBA", (across * width, down * height), (0, 0, 0, 0))
    for number, cell in enumerate(cells):
        atlas.paste(cell, (number % across * width, number // across * height))
    indexed, palette = _indexed(atlas, _palette(atlas, colors))
    alpha = atlas.getchannel("A")
    transparent = alpha.getextrema()[0] < 255
    info = {
        "frame": [width, height],
        "grid": [across, down],
        "frames": count,
        "palette": ["#%02x%02x%02x" % color for color in palette],
        "alpha": transparent and not combined,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    name = target.name
    alpha_path = target.with_name(f"{name}.alpha.png")
    if combined:
        merged = indexed.convert("RGB")
        merged.putalpha(alpha)
        merged.save(target.with_name(f"{name}.png"), optimize=True)
    else:
        indexed.save(target.with_name(f"{name}.png"), optimize=True)
    if info["alpha"]:
        alpha.save(alpha_path, optimize=True)
    elif alpha_path.exists():
        alpha_path.unlink()
    target.with_name(f"{name}.sheet.json").write_text(json.dumps(info, indent=1) + "\n")
    animation_path = target.with_name(f"{name}.anim.json")
    if animation_file is not None:
        animation_path.write_text(json.dumps(animation_file, indent=1) + "\n")
    elif times is not None and animation_path.exists():
        animation_path.unlink()
    return info


def _size(text: str) -> tuple[int, int]:
    found = re.fullmatch(r"(\d+)x(\d+)", text)
    if not found or 0 in map(int, found.groups()):
        raise argparse.ArgumentTypeError(f"frame {text!r} is not WIDTHxHEIGHT, like 32x32")
    return int(found[1]), int(found[2])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path, help="the picture or GIF")
    parser.add_argument("target", type=Path, help="where to write the sheet, without an extension")
    parser.add_argument("--frame", type=_size, help="frame size in the picture, like 32x32 (a still picture; default: the whole picture)")
    parser.add_argument("--frames", type=int, help="keep only this many frames of a still picture")
    parser.add_argument("--scale", type=float, default=1, help="resize each frame (above 1 enlarges by whole pixels; default 1)")
    parser.add_argument("--colors", type=int, default=256, help="the most colors in the palette, 2 to 256 (default 256)")
    parser.add_argument("--columns", type=int, help="frames across the sheet (default: the picture's own; 8 for a GIF)")
    parser.add_argument("--combined", action="store_true", help="write one RGBA picture instead of a color sheet and an alpha sheet")
    parser.add_argument("--animation", default="base", help="the name of a GIF's default animation (default base)")
    parser.add_argument("--no-animation", action="store_true", help="write no animation file")
    arguments = parser.parse_args(argv)
    try:
        info = convert(
            arguments.source, arguments.target, arguments.frame, arguments.scale, arguments.colors, arguments.columns, arguments.frames,
            arguments.combined, None if arguments.no_animation else arguments.animation,
        )
    except TilesheetError as error:
        print(f"{arguments.source}: {error}" if str(arguments.source) not in str(error) else str(error), file=sys.stderr)
        return 1
    print(f"{info['frames']} frames of {info['frame'][0]} by {info['frame'][1]}, {len(info['palette'])} colors, alpha: {info['alpha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Turns a QBasic tile file into a tilesheet the client draws.

The old format is what a QBasic ``PUT`` of an array of 32 by 32 pictures in SCREEN 13 (256 colors) leaves in a file::

    2 bytes     the file's own count (read, kept, and not used here)
    then one record for each tile of 1032 bytes:
      4 bytes     two 16-bit numbers the game kept with the tile (``tag`` and ``mark``; in the tileset this was written for, the second is
                  the tile's main color and the first says what kind of ground it is)
      4 bytes     QBasic's picture header: the width in bits (256 = 32 pixels of 8 bits) and the height (32)
      1024 bytes  the pixels, one byte each, left to right and then down; a byte is an index into the palette

The palette is the one the program set up: ``PALETTE r + g * 6 + b * 36`` for r, g and b from 0 to 5, each channel ``INT(c * 63 / 5)`` of
the DAC's 0..63 (so 8-bit value ``round(v * 255 / 63)``). An index above 215 is not in that palette; ``--missing`` says what to draw for it
(by default, transparent).

    python -m terraforma.tools.qbtiles MAP.MAP out/map            # writes out/map.png, out/map.alpha.png (if needed), out/map.sheet.json
    python -m terraforma.tools.qbtiles MAP.MAP out/map --preview out/map.preview.png

The sheet is the engine's tilesheet format (see ``client/static/lib/js/art.js``): an indexed PNG, the 8-bit transparency as a gray PNG (only if
some pixel is transparent), and the JSON that says how it is laid out. Standard library only.
"""

import argparse
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

SIDE = 32
RECORD = 8 + SIDE * SIDE  # a tile: 8 bytes of header, then its pixels
COLORS = 216  # 6 x 6 x 6


@dataclass(frozen=True)
class QbTile:
    tag: int  # the first number the game kept with the tile
    mark: int  # the second
    pixels: bytes  # SIDE * SIDE palette indexes


class QbError(ValueError):
    """The file is not the format."""


def channel(level: int) -> int:
    """The 8-bit value of a palette channel at $level (0 to 5): QBasic set ``INT(level * 63 / 5)`` of the DAC's 0..63."""
    return round(int(level * 63 / 5) * 255 / 63)


def color_of(index: int) -> tuple[int, int, int] | None:
    """The (r, g, b) of palette $index (``r + g * 6 + b * 36``), or None for one the palette does not have."""
    if not 0 <= index < COLORS:
        return None
    return channel(index % 6), channel(index // 6 % 6), channel(index // 36)


def read_tiles(data: bytes) -> tuple[int, list[QbTile]]:
    """The file's own count and its tiles. Raises QbError if the length is not 2 bytes and whole records, or a picture header is wrong."""
    if len(data) < 2 or (len(data) - 2) % RECORD:
        raise QbError(f"{len(data)} bytes is not 2 bytes and a whole number of {RECORD}-byte tiles")
    count = struct.unpack_from("<H", data, 0)[0]
    tiles = []
    for number in range((len(data) - 2) // RECORD):
        at = 2 + number * RECORD
        tag, mark, width_bits, height = struct.unpack_from("<HHHH", data, at)
        if (width_bits, height) != (SIDE * 8, SIDE):
            raise QbError(f"tile {number} is {width_bits // 8} by {height} pixels at 8 bits, not {SIDE} by {SIDE}")
        tiles.append(QbTile(tag, mark, data[at + 8 : at + RECORD]))
    return count, tiles


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(width: int, height: int, rows: list[bytes], palette: list[tuple[int, int, int]] | None = None) -> bytes:
    """A PNG of $rows (one byte per pixel): indexed color with $palette, or 8-bit gray when there is none."""
    header = struct.pack(">IIBBBBB", width, height, 8, 3 if palette else 0, 0, 0, 0)
    body = [_chunk(b"IHDR", header)]
    if palette:
        body.append(_chunk(b"PLTE", b"".join(bytes(color) for color in palette)))
    raw = b"".join(b"\x00" + row for row in rows)
    body += [_chunk(b"IDAT", zlib.compress(raw, 9)), _chunk(b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(body)


def sheet(tiles: list[QbTile], columns: int = 8, missing: tuple[int, int, int] | None = None):
    """Lays $tiles out in a grid of $columns. Returns (color PNG, alpha PNG or None, the sheet's JSON as a dict). An index outside the
    palette is drawn as $missing, or transparent when that is None."""
    rows_count = -(-len(tiles) // columns)
    width, height = columns * SIDE, rows_count * SIDE
    palette = [color_of(index) for index in range(COLORS)]
    spare = len(palette)  # one more entry, for $missing
    palette.append(missing or (0, 0, 0))
    colors = [bytearray(width) for _ in range(height)]
    alpha = [bytearray(b"\xff" * width) for _ in range(height)]
    for number, tile in enumerate(tiles):
        left, top = number % columns * SIDE, number // columns * SIDE
        for at, index in enumerate(tile.pixels):
            x, y = left + at % SIDE, top + at // SIDE
            if index < COLORS:
                colors[y][x] = index
            elif missing is not None:
                colors[y][x] = spare
            else:
                colors[y][x] = spare
                alpha[y][x] = 0
    transparent = any(0 in row for row in alpha)
    used = sorted({index for row in colors for index in row})
    info = {
        "frame": [SIDE, SIDE],
        "grid": [columns, rows_count],
        "frames": len(tiles),
        "palette": ["#%02x%02x%02x" % palette[index] for index in used],
        "alpha": transparent,
        "tiles": [{"frame": number, "tag": tile.tag, "mark": tile.mark} for number, tile in enumerate(tiles)],
    }
    return png(width, height, [bytes(row) for row in colors], palette), (png(width, height, [bytes(row) for row in alpha]) if transparent else None), info


def preview(tiles: list[QbTile], columns: int = 8, scale: int = 4, gap: int = 4) -> bytes:
    """A PNG to look at: the tiles enlarged $scale times on a gray ground, in the order of the sheet."""
    rows_count = -(-len(tiles) // columns)
    cell = SIDE * scale + gap
    width, height = columns * cell + gap, rows_count * cell + gap
    palette = [color_of(index) for index in range(COLORS)] + [(255, 0, 255), (60, 60, 60)]
    ground, odd = COLORS + 1, COLORS
    rows = [bytearray(bytes([ground]) * width) for _ in range(height)]
    for number, tile in enumerate(tiles):
        left, top = gap + number % columns * cell, gap + number // columns * cell
        for at, index in enumerate(tile.pixels):
            shown = index if index < COLORS else odd
            for dy in range(scale):
                line = rows[top + at // SIDE * scale + dy]
                start = left + at % SIDE * scale
                line[start : start + scale] = bytes([shown]) * scale
    return png(width, height, [bytes(row) for row in rows], palette)


def convert(source: Path, target: Path, columns: int = 8, missing: tuple[int, int, int] | None = None, preview_to: Path | None = None) -> dict:
    """Reads $source and writes ``target.png``, ``target.alpha.png`` (when some pixel is transparent) and ``target.sheet.json``."""
    count, tiles = read_tiles(source.read_bytes())
    color, alpha, info = sheet(tiles, columns, missing)
    info["source_count"] = count
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_name(target.name + ".png").write_bytes(color)
    alpha_path = target.with_name(target.name + ".alpha.png")
    if alpha is not None:
        alpha_path.write_bytes(alpha)
    elif alpha_path.exists():
        alpha_path.unlink()
    target.with_name(target.name + ".sheet.json").write_text(json.dumps(info, indent=1) + "\n")
    if preview_to is not None:
        preview_to.write_bytes(preview(tiles, columns))
    return info


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path, help="the QBasic tile file")
    parser.add_argument("target", type=Path, help="where to write the sheet, without an extension")
    parser.add_argument("--columns", type=int, default=8, help="tiles across the sheet (default 8)")
    parser.add_argument("--missing", help="a color like #ff00ff for an index the palette doesn't have (default: transparent)")
    parser.add_argument("--preview", type=Path, help="also write an enlarged picture to look at")
    arguments = parser.parse_args(argv)
    missing = None
    if arguments.missing:
        text = arguments.missing.lstrip("#")
        missing = (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    try:
        info = convert(arguments.source, arguments.target, arguments.columns, missing, arguments.preview)
    except QbError as error:
        print(f"{arguments.source}: {error}", file=sys.stderr)
        return 1
    print(f"{info['frames']} tiles, {info['grid'][0]} by {info['grid'][1]}, alpha: {info['alpha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

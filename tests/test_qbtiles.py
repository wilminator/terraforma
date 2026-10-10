"""The QBasic tile converter: the file format, the palette and the PNGs it writes."""

import json
import struct
import zlib

import pytest

from terraforma.tools import qbtiles
from terraforma.tools.qbtiles import QbError, QbTile, color_of, read_tiles


def record(tag, mark, pixels):
    return struct.pack("<HHHH", tag, mark, 256, 32) + bytes(pixels)


def file_of(*records, count=0):
    return struct.pack("<H", count) + b"".join(records)


def flat(index):
    return [index] * 1024


def decode(data):
    """A PNG's size, color type, palette and rows (unfiltered: the converter only writes filter 0)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    at, chunks = 8, {}
    while at < len(data):
        (length,) = struct.unpack_from(">I", data, at)
        kind = data[at + 4 : at + 8]
        body = data[at + 8 : at + 8 + length]
        assert struct.unpack_from(">I", data, at + 8 + length)[0] == zlib.crc32(kind + body), f"{kind} has a bad checksum"
        chunks.setdefault(kind, b"")
        chunks[kind] += body
        at += 12 + length
    width, height, depth, color_type = struct.unpack_from(">IIBB", chunks[b"IHDR"])
    raw = zlib.decompress(chunks[b"IDAT"])
    rows = [raw[row * (width + 1) + 1 : (row + 1) * (width + 1)] for row in range(height)]
    assert all(raw[row * (width + 1)] == 0 for row in range(height))
    palette = [tuple(chunks[b"PLTE"][i : i + 3]) for i in range(0, len(chunks.get(b"PLTE", b"")), 3)]
    return width, height, depth, color_type, palette, rows


def test_the_palette_is_the_six_by_six_by_six_cube_in_the_dac_s_steps():
    assert color_of(0) == (0, 0, 0) and color_of(215) == (255, 255, 255)
    assert color_of(5) == (255, 0, 0) and color_of(30) == (0, 255, 0) and color_of(180) == (0, 0, 255)
    assert color_of(1) == (49, 0, 0), "INT(1 * 63 / 5) = 12 of 63, as 8 bits"
    assert color_of(23) == (255, 150, 0), "5 + 3 * 6: red full, green three steps (INT(37.8) = 37 of 63), no blue"
    assert color_of(216) is None and color_of(255) is None


def test_the_tiles_of_a_file_are_read_with_the_numbers_the_game_kept():
    count, tiles = read_tiles(file_of(record(2, 216, flat(23)), record(0, 30, flat(30)), count=10))
    assert count == 10 and len(tiles) == 2
    assert (tiles[0].tag, tiles[0].mark, tiles[0].pixels) == (2, 216, bytes(flat(23)))
    assert (tiles[1].tag, tiles[1].mark) == (0, 30)


@pytest.mark.parametrize(
    "data, message",
    [(b"", "bytes"), (b"\x01\x00" + b"x" * 1031, "whole number"), (file_of(struct.pack("<HHHH", 0, 0, 128, 16) + bytes(1024)), "tile 0 is 16 by 16")],
)
def test_a_file_that_is_not_the_format_is_refused_with_why(data, message):
    with pytest.raises(QbError, match=message):
        read_tiles(data)


def test_a_png_decodes_to_the_rows_it_was_given():
    rows = [bytes([0, 1, 2, 3]), bytes([3, 2, 1, 0])]
    width, height, depth, color_type, palette, back = decode(qbtiles.png(4, 2, rows, [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]))
    assert (width, height, depth, color_type) == (4, 2, 8, 3) and back == rows and palette[2] == (7, 8, 9)
    assert decode(qbtiles.png(4, 2, rows))[3] == 0, "no palette: gray"


def test_a_sheet_lays_the_tiles_out_in_a_grid_and_says_how():
    tiles = [QbTile(0, 0, bytes(flat(index))) for index in (5, 30, 180)]
    color, alpha, info = qbtiles.sheet(tiles, columns=2)
    assert alpha is None and info["frame"] == [32, 32] and info["grid"] == [2, 2] and info["frames"] == 3 and info["alpha"] is False
    assert info["palette"] == ["#000000", "#ff0000", "#00ff00", "#0000ff"], "the colors used, by index (black fills the empty fourth place)"
    width, height, _, _, palette, rows = decode(color)
    assert (width, height) == (64, 64)
    assert palette[rows[0][0]] == (255, 0, 0) and palette[rows[0][40]] == (0, 255, 0) and palette[rows[40][0]] == (0, 0, 255)


def test_an_index_the_palette_does_not_have_is_transparent_unless_a_color_is_asked_for():
    pixels = bytearray(flat(23))
    pixels[0] = 216
    color, alpha, info = qbtiles.sheet([QbTile(0, 0, bytes(pixels))], columns=1)
    assert info["alpha"] is True
    _, _, _, _, _, alpha_rows = decode(alpha)
    assert alpha_rows[0][0] == 0 and alpha_rows[0][1] == 255
    color2, alpha2, info2 = qbtiles.sheet([QbTile(0, 0, bytes(pixels))], columns=1, missing=(255, 0, 255))
    assert alpha2 is None and info2["alpha"] is False
    _, _, _, _, palette, rows = decode(color2)
    assert palette[rows[0][0]] == (255, 0, 255)


def test_convert_writes_the_sheet_the_alpha_and_the_preview(tmp_path):
    source = tmp_path / "MAP.MAP"
    pixels = bytearray(flat(30))
    pixels[1] = 250
    source.write_bytes(file_of(record(3, 30, pixels), record(0, 23, flat(23)), count=10))
    info = qbtiles.convert(source, tmp_path / "out" / "tiles", columns=2, preview_to=tmp_path / "look.png")
    sheet = json.loads((tmp_path / "out" / "tiles.sheet.json").read_text())
    assert sheet == info and sheet["frames"] == 2 and sheet["tiles"][0] == {"frame": 0, "tag": 3, "mark": 30} and sheet["source_count"] == 10
    assert decode((tmp_path / "out" / "tiles.png").read_bytes())[:2] == (64, 32)
    assert (tmp_path / "out" / "tiles.alpha.png").exists(), "one pixel has no color"
    assert decode((tmp_path / "look.png").read_bytes())[0] > 64, "enlarged"


def test_the_command_line_says_what_is_wrong_with_a_file(tmp_path, capsys):
    bad = tmp_path / "bad.map"
    bad.write_bytes(b"abc")
    assert qbtiles.main([str(bad), str(tmp_path / "out")]) == 1
    assert "not 2 bytes and a whole number" in capsys.readouterr().err
    good = tmp_path / "good.map"
    good.write_bytes(file_of(record(0, 0, flat(1))))
    assert qbtiles.main([str(good), str(tmp_path / "out")]) == 0
    assert "1 tiles" in capsys.readouterr().out

"""The color map format and its tool: making, checking, joining and previewing maps."""

import json

import pytest
from PIL import Image

from terraforma.client import ClientError, check_game_client
from terraforma.colormaps import ColorMapError, apply, check_color_map, check_color_map_folder, normalize, rgb
from terraforma.tools import colormap
from terraforma.tools.colormap import ColorMapToolError, check, main, make, merge, palette_of, preview, read_map

PALETTE = ["#3050a0", "#7090e0", "#101010", "#808080"]


def sheet(tmp_path, palette=PALETTE, name="knight"):
    """A one-row sheet of a pixel for each palette color, written as the tilesheet tool would (indexed picture, no alpha)."""
    picture = Image.new("RGB", (len(palette), 1))
    for number, color in enumerate(palette):
        picture.putpixel((number, 0), rgb(color))
    picture.save(tmp_path / f"{name}.png")
    info = {"frame": [len(palette), 1], "grid": [1, 1], "frames": 1, "palette": palette, "alpha": False}
    (tmp_path / f"{name}.sheet.json").write_text(json.dumps(info))
    return tmp_path / name


def test_normalize_accepts_what_the_browser_does():
    assert normalize("#3050A0") == "#3050a0"
    assert normalize(" 3050a0 ") == "#3050a0"
    for bad in ("#305", "red", "#3050a0ff", "", None, 12, "#30g0a0"):
        with pytest.raises(ColorMapError):
            normalize(bad)


def test_check_color_map_cleans_and_refuses():
    assert check_color_map({"#3050A0": "A03030"}) == {"#3050a0": "#a03030"}
    with pytest.raises(ColorMapError, match="listed twice"):
        check_color_map({"#3050a0": "#000000", "#3050A0": "#ffffff"})
    with pytest.raises(ColorMapError, match="object"):
        check_color_map(["#3050a0"])
    with pytest.raises(ColorMapError, match="not a color"):
        check_color_map({"#3050a0": "blue"})


def test_check_color_map_folder_names_the_file(tmp_path):
    (tmp_path / "good.colors.json").write_text('{"#3050a0": "#a03030"}')
    (tmp_path / "other.json").write_text("not a map")  # only *.colors.json is a map
    check_color_map_folder(tmp_path)
    (tmp_path / "bad.colors.json").write_text('{"#3050a0": "blue"}')
    with pytest.raises(ColorMapError, match=r"bad\.colors\.json"):
        check_color_map_folder(tmp_path)
    (tmp_path / "bad.colors.json").write_text("{")
    with pytest.raises(ColorMapError, match=r"bad\.colors\.json: not JSON"):
        check_color_map_folder(tmp_path)


def test_the_server_refuses_to_start_on_a_bad_map(tmp_path):
    from terraforma.game import Game

    (tmp_path / "bad.colors.json").write_text('{"#3050a0": "blue"}')
    with pytest.raises(ClientError, match=r"assets_dir: bad\.colors\.json"):
        check_game_client(Game(name="Bad", assets_dir=tmp_path))


def test_the_example_map_is_fine():
    from terraforma.example import HERE

    check_color_map_folder(HERE / "assets")


def test_apply_snaps_to_the_palette_then_swaps():
    palette = [rgb(color) for color in PALETTE]
    swaps = {rgb("#3050a0"): rgb("#a03030")}
    assert apply(rgb("#3050a0"), palette, swaps) == rgb("#a03030")
    assert apply((50, 82, 158), palette, swaps) == rgb("#a03030")  # nearly the blue
    assert apply(rgb("#101010"), palette, swaps) == rgb("#101010")  # not listed: unchanged
    assert apply((1, 2, 3), [], swaps) == (1, 2, 3)  # no palette: nothing to snap to


def test_make_a_hue_turn_changes_colors_and_leaves_grays():
    colors = make(PALETTE, hue=180)
    assert list(colors) == ["#3050a0", "#7090e0"]  # grays have no hue; they are not listed
    assert colors["#3050a0"] != "#3050a0"
    assert make(["#ff0000", "#808080"], hue=120) == {"#ff0000": "#00ff00"}
    assert make(["#ff0000"], hue=360 + 120) == {"#ff0000": "#00ff00"}
    assert make(["#ff0000"], hue=-120) == {"#ff0000": "#0000ff"}


def test_make_saturation_and_lightness():
    assert make(["#ff0000"], saturation=0) == {"#ff0000": "#808080"}
    assert make(["#808080"], lightness=0) == {"#808080": "#000000"}
    assert make(["#ff8080"], lightness=10) == {"#ff8080": "#ffffff"}  # clamped


def test_make_only_skip_and_swap():
    only = make(PALETTE, hue=180, only=["#3050A0"])
    assert list(only) == ["#3050a0"]
    skipped = make(PALETTE, hue=180, skip=["#3050a0"])
    assert list(skipped) == ["#7090e0"]
    swapped = make(PALETTE, hue=180, only=["#3050a0"], swaps={"#3050a0": "#112233", "#101010": "#202020"})
    assert swapped == {"#3050a0": "#112233", "#101010": "#202020"}  # a swap wins, and works on a color the shifts skip
    assert make(PALETTE, swaps={"#3050a0": "#3050A0", "#101010": "#000000"}) == {"#101010": "#000000"}  # an entry that changes nothing is left out


def test_make_refuses():
    with pytest.raises(ColorMapToolError, match="nothing changes"):
        make(PALETTE)
    with pytest.raises(ColorMapToolError, match="not in the sheet's palette"):
        make(PALETTE, hue=10, only=["#123456"])
    with pytest.raises(ColorMapToolError, match="not in the sheet's palette"):
        make(PALETTE, swaps={"#123456": "#000000"})
    with pytest.raises(ColorMapToolError, match="factors"):
        make(PALETTE, saturation=-1)


def test_check_flags_colors_the_palette_lacks():
    assert check({"#3050a0": "#a03030"}, PALETTE) == []
    assert check({"#123456": "#a03030"}, PALETTE) == ["#123456 is not in the sheet's palette, so this entry does nothing"]
    assert check({"#123456": "#a03030"}) == []  # without a sheet only the format is checked


def test_merge_does_the_first_then_the_second():
    first = {"#3050a0": "#a03030", "#101010": "#202020"}
    second = {"#a03030": "#00ff00", "#808080": "#ffffff", "#202020": "#101010"}
    # #101010 goes round to itself, so it drops out; without the palette every entry of the second map is kept
    assert merge(first, second) == {"#3050a0": "#00ff00", "#808080": "#ffffff", "#a03030": "#00ff00", "#202020": "#101010"}
    # with it, the entries for colors that are no palette color (the ones the first map makes) go
    assert merge(first, second, PALETTE) == {"#3050a0": "#00ff00", "#808080": "#ffffff"}
    assert merge({}, second) == second
    assert merge(first, {}) == first


def test_palette_of_takes_any_name_for_the_sheet(tmp_path):
    base = sheet(tmp_path)
    for name in (base, base.with_name("knight.png"), base.with_name("knight.sheet.json")):
        assert palette_of(name) == PALETTE
    with pytest.raises(ColorMapToolError, match="no such file"):
        palette_of(tmp_path / "missing")
    (tmp_path / "odd.sheet.json").write_text('{"frames": 1}')
    with pytest.raises(ColorMapToolError, match="palette"):
        palette_of(tmp_path / "odd")


def test_the_command_line_makes_checks_and_joins(tmp_path, capsys):
    base = sheet(tmp_path)
    assert main(["colors", str(base)]) == 0
    assert capsys.readouterr().out.split() == PALETTE

    assert main(["make", str(base), str(tmp_path / "red"), "--hue", "150", "--swap", "#101010=#202020"]) == 0
    assert "colors change" in capsys.readouterr().out
    made = json.loads((tmp_path / "red.colors.json").read_text())
    assert made["#101010"] == "#202020" and set(made) == {"#3050a0", "#7090e0", "#101010"}

    assert main(["check", str(tmp_path / "red.colors.json"), "--sheet", str(base)]) == 0
    assert "1 maps fine" in capsys.readouterr().out

    (tmp_path / "off.colors.json").write_text('{"#123456": "#000000"}')
    assert main(["check", str(tmp_path / "red"), str(tmp_path / "off"), "--sheet", str(base)]) == 1
    assert "off.colors.json: #123456 is not in the sheet's palette" in capsys.readouterr().err

    (tmp_path / "dark.colors.json").write_text('{"#202020": "#000000"}')
    assert main(["merge", str(tmp_path / "red"), str(tmp_path / "dark"), str(tmp_path / "both"), "--sheet", str(base)]) == 0
    assert read_map(tmp_path / "both")["#101010"] == "#000000"


def test_the_command_line_reports_errors_without_a_traceback(tmp_path, capsys):
    base = sheet(tmp_path)
    assert main(["make", str(base), str(tmp_path / "x")]) == 1
    assert "nothing changes" in capsys.readouterr().err
    assert main(["make", str(base), str(tmp_path / "x"), "--hue", "9", "--only", "nope"]) == 1
    assert "not a color" in capsys.readouterr().err
    assert main(["check", str(tmp_path / "missing")]) == 1
    assert "no such file" in capsys.readouterr().err
    (tmp_path / "bad.colors.json").write_text('{"#3050a0": "blue"}')
    assert main(["check", str(tmp_path / "bad")]) == 1
    assert "not a color" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # argparse's own refusal of a swap with no =
        main(["make", str(base), str(tmp_path / "x"), "--swap", "#3050a0"])


def test_preview_draws_what_the_browser_draws(tmp_path):
    base = sheet(tmp_path)
    (tmp_path / "a.colors.json").write_text('{"#3050a0": "#a03030"}')
    preview(base, read_map(tmp_path / "a"), tmp_path / "look.png")
    with Image.open(tmp_path / "look.png") as drawn:
        assert drawn.size == (4, 1)
        assert [drawn.getpixel((x, 0)) for x in range(4)] == [(160, 48, 48, 255), (112, 144, 224, 255), (16, 16, 16, 255), (128, 128, 128, 255)]
    preview(base, read_map(tmp_path / "a"), tmp_path / "big.png", scale=3)
    with Image.open(tmp_path / "big.png") as drawn:
        assert drawn.size == (12, 3) and drawn.getpixel((2, 2)) == (160, 48, 48, 255)


def test_preview_keeps_the_alpha_sheet_and_snaps_near_colors(tmp_path):
    base = sheet(tmp_path)
    near = Image.new("RGB", (2, 1))
    near.putpixel((0, 0), (48, 82, 158))  # nearly the first color
    near.putpixel((1, 0), (16, 16, 16))
    near.save(tmp_path / "knight.png")
    alpha = Image.new("L", (2, 1))
    alpha.putpixel((0, 0), 255)
    alpha.putpixel((1, 0), 100)
    alpha.save(tmp_path / "knight.alpha.png")
    preview(base, {"#3050a0": "#a03030"}, tmp_path / "look.png")
    with Image.open(tmp_path / "look.png") as drawn:
        assert drawn.getpixel((0, 0)) == (160, 48, 48, 255)
        assert drawn.getpixel((1, 0)) == (16, 16, 16, 100)


def test_preview_refuses_a_bad_scale(tmp_path):
    base = sheet(tmp_path)
    with pytest.raises(ColorMapToolError, match="whole number"):
        preview(base, {}, tmp_path / "x.png", scale=0)


def test_the_tool_needs_no_pillow_except_to_preview():
    import ast
    import inspect

    top = [n for n in ast.parse(inspect.getsource(colormap)).body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("PIL" in ast.dump(n) for n in top)

"""The tilesheet tool: pictures and GIFs become an indexed color sheet, an alpha sheet, a sheet file and (for a GIF) an animation file."""

import json

import pytest
from PIL import Image

from terraforma.animations import check_animation_folder, check_animations
from terraforma.tools import tilesheet
from terraforma.tools.tilesheet import TilesheetError, convert, main

pytestmark = pytest.mark.platform

RED, GREEN, BLUE, YELLOW = (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)


def solid(size, color):
    return Image.new("RGBA", size, color)


def strip_of(*colors, side=8):
    """A picture one frame tall: a $side by $side square of each color, left to right."""
    picture = Image.new("RGBA", (side * len(colors), side))
    for number, color in enumerate(colors):
        picture.paste(solid((side, side), color), (number * side, 0))
    return picture


def save(tmp_path, picture, name="in.png"):
    path = tmp_path / name
    picture.save(path)
    return path


def gif(tmp_path, colors, durations, name="in.gif", **options):
    frames = [solid((8, 8), color).convert("P") for color in colors]
    path = tmp_path / name
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, **options)
    return path


def read(tmp_path, name="out"):
    """What the tool wrote: (color image, alpha image or None, sheet dict, animation dict or None)."""
    with Image.open(tmp_path / f"{name}.png") as picture:
        color = picture.copy()
    alpha_path = tmp_path / f"{name}.alpha.png"
    alpha = None
    if alpha_path.exists():
        with Image.open(alpha_path) as picture:
            alpha = picture.copy()
    info = json.loads((tmp_path / f"{name}.sheet.json").read_text())
    anim_path = tmp_path / f"{name}.anim.json"
    return color, alpha, info, json.loads(anim_path.read_text()) if anim_path.exists() else None


def pixels_of(color, alpha=None):
    """What the browser draws: the indexed sheet in colors, with the alpha sheet's gray as transparency."""
    rgba = color.convert("RGBA")
    if alpha is not None:
        rgba.putalpha(alpha.convert("L"))
    return rgba


def rgba_pixels(picture):
    raw = picture.tobytes()
    return list(zip(raw[0::4], raw[1::4], raw[2::4], raw[3::4]))


# --- a picture cut into frames ------------------------------------------------------------------------------------------------------------

def test_a_picture_is_cut_into_frames_left_to_right_and_down(tmp_path):
    source = save(tmp_path, strip_of(RED, GREEN, BLUE, YELLOW, side=4))
    convert(source, tmp_path / "out", frame=(4, 4))
    color, alpha, info, animations = read(tmp_path)
    assert info == {"frame": [4, 4], "grid": [4, 1], "frames": 4, "palette": info["palette"], "alpha": False}
    assert sorted(info["palette"]) == ["#0000ff", "#00ff00", "#ff0000", "#ffff00"]
    assert color.mode == "P" and color.size == (16, 4)
    assert alpha is None and animations is None
    assert pixels_of(color).getpixel((9, 1)) == BLUE


def test_without_a_frame_size_the_whole_picture_is_one_frame(tmp_path):
    convert(save(tmp_path, solid((10, 6), RED)), tmp_path / "out")
    assert read(tmp_path)[2]["frame"] == [10, 6]
    assert read(tmp_path)[2]["frames"] == 1


def test_a_picture_that_is_not_whole_frames_is_refused(tmp_path):
    with pytest.raises(TilesheetError, match="10 by 6"):
        convert(save(tmp_path, solid((10, 6), RED)), tmp_path / "out", frame=(4, 6))


def test_frames_limits_how_many_cells_are_kept(tmp_path):
    convert(save(tmp_path, strip_of(RED, GREEN, BLUE, side=4)), tmp_path / "out", frame=(4, 4), frames=2)
    color, _, info, _ = read(tmp_path)
    assert info["frames"] == 2 and info["grid"] == [2, 1] and color.size == (8, 4)
    assert "#0000ff" not in info["palette"]


def test_frames_more_than_the_picture_holds_is_refused(tmp_path):
    with pytest.raises(TilesheetError, match="3"):
        convert(save(tmp_path, strip_of(RED, GREEN, side=4)), tmp_path / "out", frame=(4, 4), frames=3)


def test_columns_lay_the_sheet_out_again(tmp_path):
    convert(save(tmp_path, strip_of(RED, GREEN, BLUE, YELLOW, RED, side=4)), tmp_path / "out", frame=(4, 4), columns=2)
    color, alpha, info, _ = read(tmp_path)
    assert info["grid"] == [2, 3] and info["frames"] == 5 and color.size == (8, 12)
    assert pixels_of(color, alpha).getpixel((1, 9)) == RED  # frame 4: first column, third row
    assert pixels_of(color, alpha).getpixel((5, 9))[3] == 0  # the sixth cell is empty


def test_a_still_picture_keeps_its_own_columns(tmp_path):
    picture = Image.new("RGBA", (8, 8))
    for cell, color in enumerate([RED, GREEN, BLUE, YELLOW]):
        picture.paste(solid((4, 4), color), (cell % 2 * 4, cell // 2 * 4))
    convert(save(tmp_path, picture), tmp_path / "out", frame=(4, 4))
    assert read(tmp_path)[2]["grid"] == [2, 2]


# --- scale ---------------------------------------------------------------------------------------------------------------------------------

def test_scale_enlarges_each_frame_by_whole_pixels(tmp_path):
    picture = Image.new("RGBA", (2, 1))
    picture.putpixel((0, 0), RED)
    picture.putpixel((1, 0), BLUE)
    convert(save(tmp_path, picture), tmp_path / "out", frame=(1, 1), scale=3)
    color, _, info, _ = read(tmp_path)
    assert info["frame"] == [3, 3] and color.size == (6, 3)
    shown = pixels_of(color)
    assert {shown.getpixel((x, y)) for x in range(3) for y in range(3)} == {RED}
    assert {shown.getpixel((x, y)) for x in range(3, 6) for y in range(3)} == {BLUE}


def test_scale_below_one_shrinks_each_frame(tmp_path):
    convert(save(tmp_path, strip_of(RED, GREEN, side=8)), tmp_path / "out", frame=(8, 8), scale=0.5)
    color, _, info, _ = read(tmp_path)
    assert info["frame"] == [4, 4] and color.size == (8, 4)
    assert pixels_of(color).getpixel((0, 0)) == RED and pixels_of(color).getpixel((7, 3)) == GREEN


def test_a_scale_that_leaves_nothing_is_refused(tmp_path):
    with pytest.raises(TilesheetError, match="scale"):
        convert(save(tmp_path, solid((4, 4), RED)), tmp_path / "out", scale=0.01)
    with pytest.raises(TilesheetError, match="scale"):
        convert(save(tmp_path, solid((4, 4), RED)), tmp_path / "out", scale=0)


# --- colors --------------------------------------------------------------------------------------------------------------------------------

def gradient():
    picture = Image.new("RGBA", (64, 4))
    for x in range(64):
        for y in range(4):
            picture.putpixel((x, y), (x * 4, 255 - x * 4, 128, 255))
    return picture


def test_a_picture_with_few_colors_keeps_them_exactly(tmp_path):
    convert(save(tmp_path, strip_of(RED, GREEN, BLUE, side=4)), tmp_path / "out", frame=(4, 4), colors=16)
    color, _, info, _ = read(tmp_path)
    assert len(info["palette"]) == 3
    assert set(rgba_pixels(pixels_of(color))) == {RED, GREEN, BLUE}


def test_colors_limits_the_palette(tmp_path):
    convert(save(tmp_path, gradient()), tmp_path / "out", colors=4)
    color, _, info, _ = read(tmp_path)
    assert 1 < len(info["palette"]) <= 4
    shown = {pixel[:3] for pixel in rgba_pixels(pixels_of(color))}
    assert shown == {tuple(int(entry[at : at + 2], 16) for at in (1, 3, 5)) for entry in info["palette"]}


def test_the_palette_lists_only_colors_the_sheet_uses(tmp_path):
    convert(save(tmp_path, gradient()), tmp_path / "out", colors=8)
    color, _, info, _ = read(tmp_path)
    assert len(info["palette"]) == len(set(color.tobytes()))


def test_the_same_picture_gives_the_same_sheet(tmp_path):
    source = save(tmp_path, gradient())
    convert(source, tmp_path / "one", colors=6)
    convert(source, tmp_path / "two", colors=6)
    assert (tmp_path / "one.png").read_bytes() == (tmp_path / "two.png").read_bytes()
    assert (tmp_path / "one.sheet.json").read_text() == (tmp_path / "two.sheet.json").read_text()


@pytest.mark.parametrize("colors", [0, 1, 257])
def test_colors_out_of_range_is_refused(tmp_path, colors):
    with pytest.raises(TilesheetError, match="colors"):
        convert(save(tmp_path, solid((4, 4), RED)), tmp_path / "out", colors=colors)


# --- alpha ---------------------------------------------------------------------------------------------------------------------------------

def test_transparency_goes_to_a_separate_alpha_sheet(tmp_path):
    picture = solid((4, 2), RED)
    picture.putpixel((0, 0), (255, 0, 255, 0))  # fully clear, with a color that must not reach the palette
    picture.putpixel((1, 0), (0, 0, 255, 100))  # soft
    convert(save(tmp_path, picture), tmp_path / "out")
    color, alpha, info, _ = read(tmp_path)
    assert info["alpha"] is True
    assert alpha.mode == "L" and alpha.size == color.size
    assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((1, 0)) == 100 and alpha.getpixel((2, 0)) == 255
    assert "#ff00ff" not in info["palette"]
    assert pixels_of(color, alpha).getpixel((1, 0)) == (0, 0, 255, 100)


def test_an_opaque_picture_writes_no_alpha_sheet_and_removes_an_old_one(tmp_path):
    (tmp_path / "out.alpha.png").write_bytes(b"old")
    convert(save(tmp_path, solid((4, 4), RED)), tmp_path / "out")
    assert not (tmp_path / "out.alpha.png").exists()
    assert read(tmp_path)[2]["alpha"] is False


def test_empty_cells_of_a_short_last_row_are_transparent(tmp_path):
    convert(save(tmp_path, strip_of(RED, GREEN, BLUE, side=4)), tmp_path / "out", frame=(4, 4), columns=2)
    _, alpha, info, _ = read(tmp_path)
    assert info["alpha"] is True
    assert alpha.getpixel((5, 5)) == 0 and alpha.getpixel((1, 1)) == 255


def test_a_picture_with_no_visible_pixel_is_refused(tmp_path):
    with pytest.raises(TilesheetError, match="nothing"):
        convert(save(tmp_path, solid((4, 4), (0, 0, 0, 0))), tmp_path / "out")


def test_combined_writes_one_rgba_picture(tmp_path):
    picture = solid((4, 2), RED)
    picture.putpixel((3, 1), (0, 0, 0, 0))
    convert(save(tmp_path, picture), tmp_path / "out", combined=True)
    color, alpha, info, _ = read(tmp_path)
    assert alpha is None and info["alpha"] is False
    assert color.mode == "RGBA"
    assert color.getpixel((0, 0)) == RED and color.getpixel((3, 1))[3] == 0
    assert info["palette"] == ["#ff0000"]


# --- GIFs ----------------------------------------------------------------------------------------------------------------------------------

def test_a_gif_becomes_one_frame_each_and_a_default_animation(tmp_path):
    convert(gif(tmp_path, [RED, GREEN, BLUE], [100, 100, 300], loop=0), tmp_path / "out")
    color, _, info, animations = read(tmp_path)
    assert info["frame"] == [8, 8] and info["frames"] == 3 and info["grid"] == [3, 1]
    assert [pixels_of(color).getpixel((x * 8, 0)) for x in range(3)] == [RED, GREEN, BLUE]
    assert animations == {"animations": {"base": {"frames": [0, 1, {"frame": 2, "ms": 300}], "ms": 100, "loop": True}}}
    check_animations(animations, frames=3)


def test_a_gif_that_does_not_loop_holds_its_last_frame(tmp_path):
    convert(gif(tmp_path, [RED, GREEN], [50, 50]), tmp_path / "out")
    animations = read(tmp_path)[3]
    assert animations["animations"]["base"]["loop"] is False
    assert animations["animations"]["base"]["ms"] == 50


def test_a_gif_frame_with_no_time_plays_at_a_tenth_of_a_second(tmp_path):
    convert(gif(tmp_path, [RED, GREEN], [0, 0], loop=0), tmp_path / "out")
    assert read(tmp_path)[3]["animations"]["base"]["ms"] == 100


def test_animation_names_the_animation(tmp_path):
    convert(gif(tmp_path, [RED, GREEN], [100, 100], loop=0), tmp_path / "out", animation="stand_down")
    assert list(read(tmp_path)[3]["animations"]) == ["stand_down"]


def test_a_bad_animation_name_is_refused(tmp_path):
    with pytest.raises(TilesheetError, match="name"):
        convert(gif(tmp_path, [RED, GREEN], [100, 100]), tmp_path / "out", animation="Stand Down")


def test_no_animation_skips_the_file_and_removes_an_old_one(tmp_path):
    (tmp_path / "out.anim.json").write_text("{}")
    convert(gif(tmp_path, [RED, GREEN], [100, 100]), tmp_path / "out", animation=None)
    assert not (tmp_path / "out.anim.json").exists()
    assert read(tmp_path)[2]["frames"] == 2


def test_a_still_picture_writes_no_animation_file_and_keeps_an_old_one(tmp_path):
    # a hand-written animation file belongs to the sheet; only a GIF makes a default one
    (tmp_path / "out.anim.json").write_text('{"animations": {"base": {"frames": [0]}}}')
    convert(save(tmp_path, solid((4, 4), RED)), tmp_path / "out")
    assert json.loads((tmp_path / "out.anim.json").read_text())["animations"]["base"]["frames"] == [0]


def test_a_gif_scales_and_limits_colors_like_a_picture(tmp_path):
    convert(gif(tmp_path, [RED, GREEN, BLUE], [100, 100, 100], loop=0), tmp_path / "out", scale=2, colors=2)
    color, _, info, _ = read(tmp_path)
    assert info["frame"] == [16, 16] and color.size == (48, 16)
    assert len(info["palette"]) <= 2


def test_a_gif_with_a_frame_size_is_refused(tmp_path):
    with pytest.raises(TilesheetError, match="GIF"):
        convert(gif(tmp_path, [RED, GREEN], [100, 100]), tmp_path / "out", frame=(4, 4))


def test_a_gif_with_too_many_frames_for_an_animation_is_refused(tmp_path):
    many = [(number % 256, number // 256 * 100, 0, 255) for number in range(257)]
    with pytest.raises(TilesheetError, match="--no-animation"):
        convert(gif(tmp_path, many, [100] * 257), tmp_path / "out")
    convert(gif(tmp_path, many, [100] * 257), tmp_path / "out", animation=None)
    assert read(tmp_path)[2]["frames"] == 257


def test_a_gif_with_transparency_gets_an_alpha_sheet(tmp_path):
    frames = []
    for color in (RED, GREEN):
        picture = solid((8, 8), (0, 0, 0, 0))
        picture.paste(solid((4, 4), color), (0, 0))
        frames.append(picture)
    frames[0].save(tmp_path / "in.gif", save_all=True, append_images=frames[1:], duration=100, loop=0, disposal=2)
    convert(tmp_path / "in.gif", tmp_path / "out")
    color, alpha, info, _ = read(tmp_path)
    assert info["alpha"] is True
    assert alpha.getpixel((7, 7)) == 0 and alpha.getpixel((1, 1)) == 255
    assert pixels_of(color, alpha).getpixel((9, 1)) == GREEN


# --- the whole thing, as the engine reads it -------------------------------------------------------------------------------------------------

def test_what_the_tool_writes_passes_the_engines_checks(tmp_path):
    convert(gif(tmp_path, [RED, GREEN, BLUE], [100, 100, 100], loop=0), tmp_path / "knight")
    check_animation_folder(tmp_path)  # checks knight.anim.json against knight.sheet.json's frame count


def test_the_sheet_file_has_only_the_fields_the_client_reads(tmp_path):
    convert(save(tmp_path, strip_of(RED, GREEN, side=4)), tmp_path / "out", frame=(4, 4))
    assert set(read(tmp_path)[2]) == {"frame", "grid", "frames", "palette", "alpha"}


def test_a_folder_that_does_not_exist_is_made(tmp_path):
    convert(save(tmp_path, solid((4, 4), RED)), tmp_path / "deep" / "er" / "out")
    assert (tmp_path / "deep" / "er" / "out.png").exists()


def test_a_file_that_is_not_a_picture_is_refused(tmp_path):
    (tmp_path / "in.png").write_text("not a picture")
    with pytest.raises(TilesheetError, match="in.png"):
        convert(tmp_path / "in.png", tmp_path / "out")
    with pytest.raises(TilesheetError, match="missing.png"):
        convert(tmp_path / "missing.png", tmp_path / "out")


# --- the command line ----------------------------------------------------------------------------------------------------------------------

def test_the_command_line_writes_the_sheet(tmp_path, capsys):
    source = save(tmp_path, strip_of(RED, GREEN, side=4))
    code = main([str(source), str(tmp_path / "out"), "--frame", "4x4", "--scale", "2", "--colors", "8", "--columns", "1"])
    assert code == 0
    info = read(tmp_path)[2]
    assert info["frame"] == [8, 8] and info["grid"] == [1, 2]
    assert "2 frames" in capsys.readouterr().out


def test_the_command_line_takes_combined_and_no_animation(tmp_path):
    source = gif(tmp_path, [RED, GREEN], [100, 100], loop=0)
    assert main([str(source), str(tmp_path / "out"), "--combined", "--no-animation"]) == 0
    color, alpha, _, animations = read(tmp_path)
    assert color.mode == "RGBA" and alpha is None and animations is None


def test_the_command_line_reports_a_problem_and_fails(tmp_path, capsys):
    assert main([str(tmp_path / "missing.png"), str(tmp_path / "out")]) == 1
    assert "missing.png" in capsys.readouterr().err


@pytest.mark.parametrize("size", ["4", "4x", "0x4", "axb", "4x4x4"])
def test_a_bad_frame_size_is_refused(tmp_path, size, capsys):
    with pytest.raises(SystemExit):
        main([str(save(tmp_path, solid((4, 4), RED))), str(tmp_path / "out"), "--frame", size])
    assert "frame" in capsys.readouterr().err

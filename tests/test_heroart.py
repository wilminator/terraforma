"""The hero art tool: a looks file becomes a fight sheet and a map sheet for each class, with the back view the engine insists on."""

import json

import pytest
from PIL import Image

from terraforma.animations import check_animation_folder, check_hero_back_views
from terraforma.tools import heroart
from terraforma.tools.heroart import BODIES, CARRY, HEADS, OFFHANDS, POSES, HeroArtError, Look, draw, fight_figure, main, map_figure, read_looks

pytestmark = pytest.mark.platform

LOOKS = {
    "fighter": {"colors": {"cloth": "#7e8aa0", "accent": "#c03030"}, "head": "helm", "weapon": "sword", "offhand": "kite", "body": "tabard"},
    "scout": {"colors": {"cloth": "#4c7c3e"}, "head": "feather_cap", "weapon": "bow", "melee": "dagger", "ranged": "bow", "body": "quiver"},
    "monk": {"colors": {"skin": "#d8a070"}, "head": "headband", "ranged": "spark", "body": "sash", "bald": True, "down_cap": "#c03030"},
}


def write_looks(tmp_path, looks=LOOKS, name="looks.json"):
    path = tmp_path / name
    path.write_text(json.dumps(looks))
    return path


def pixels(folder, name):
    color = Image.open(folder / f"{name}.png").convert("RGBA")
    color.putalpha(Image.open(folder / f"{name}.alpha.png").convert("L"))
    return color.size, color.tobytes()


def frames(look):
    return [fight_figure(pose, look).rows for pose in POSES] + [map_figure(facing, step, look).rows for facing in ("down", "up", "right") for step in (0, 1, 2)]


def test_every_piece_draws_in_every_pose_and_changes_the_picture():
    plain = frames(Look(colors={}))
    for field, table in (("head", HEADS), ("weapon", CARRY), ("offhand", OFFHANDS), ("body", BODIES)):
        for name in table:
            dressed = frames(Look(colors={}, **{field: name}))
            assert dressed != plain or (field, name) == ("head", "hair"), f"{field} {name} draws nothing"


def test_a_figure_is_32_by_32_and_has_an_outline_and_clear_corners():
    for rows in frames(Look(colors={}, head="helm", weapon="sword", offhand="kite", body="tabard")):
        assert len(rows) == 32 and all(len(row) == 32 for row in rows)
        assert rows[0][0] is None and rows[31][31] is None
        assert any(color == heroart.OUT for row in rows for color in row)


def test_a_class_is_written_as_a_fight_sheet_and_a_map_sheet_the_engine_checks(tmp_path):
    draw("fighter", read_looks(write_looks(tmp_path))["fighter"], tmp_path / "assets")
    folder = tmp_path / "assets"
    fight, map_ = (json.loads((folder / f"fighter_{kind}.sheet.json").read_text()) for kind in ("fight", "map"))
    assert (fight["frames"], fight["grid"], fight["frame"], fight["alpha"]) == (12, [4, 3], [32, 32], True)
    assert (map_["frames"], map_["grid"], map_["frame"], map_["alpha"]) == (9, [3, 3], [32, 32], True)
    assert 4 <= len(fight["palette"]) <= 40
    for suffix in (".png", ".alpha.png", ".anim.json"):
        assert (folder / f"fighter_fight{suffix}").exists() and (folder / f"fighter_map{suffix}").exists()
    check_animation_folder(folder)  # (the server does this when a game starts)


def test_the_fight_sheet_has_the_back_view_a_hero_personality_needs(tmp_path):
    draw("fighter", read_looks(write_looks(tmp_path))["fighter"], tmp_path)
    animations = json.loads((tmp_path / "fighter_fight.anim.json").read_text())["animations"]
    assert {"base_up", "hit_up", "attack_close_up", "base", "hit", "die", "attack_close", "spell", "item", "attack_shoot"} <= set(animations)
    seed = {"jobs": [{"key": "fighter"}], "personalities": [{"key": "fighter", "fight_art": {"sheet": "fighter_fight", "animations": "fighter_fight"}}]}
    check_hero_back_views(seed, tmp_path)


def test_the_map_sheet_stands_and_walks_in_every_direction(tmp_path):
    draw("monk", read_looks(write_looks(tmp_path))["monk"], tmp_path)
    animation = json.loads((tmp_path / "monk_map.anim.json").read_text())
    known = set(animation["animations"]) | set(animation["aliases"])
    assert {f"{move}_{way}" for move in ("stand", "move") for way in ("up", "down", "left", "right")} <= known


def test_drawing_again_gives_the_same_pictures(tmp_path):
    looks = read_looks(write_looks(tmp_path))
    for key in looks:
        draw(key, looks[key], tmp_path / "one")
        draw(key, looks[key], tmp_path / "two")
        for kind in ("fight", "map"):
            assert pixels(tmp_path / "one", f"{key}_{kind}") == pixels(tmp_path / "two", f"{key}_{kind}")
            assert (tmp_path / "one" / f"{key}_{kind}.anim.json").read_text() == (tmp_path / "two" / f"{key}_{kind}.anim.json").read_text()


def test_classes_with_different_looks_get_different_pictures(tmp_path):
    for key, look in read_looks(write_looks(tmp_path)).items():
        draw(key, look, tmp_path)
    fights = {key: pixels(tmp_path, f"{key}_fight") for key in LOOKS}
    assert len(set(fights.values())) == len(LOOKS)


def test_a_look_names_its_own_colors_and_keeps_the_shared_ones(tmp_path):
    look = read_looks(write_looks(tmp_path))["fighter"]
    assert look.colors["cloth"] == "#7e8aa0" and look.colors["skin"] == heroart.BASE["skin"]
    assert look.colors["hood"] == look.colors["cloth"] and look.colors["mask"] == look.colors["accent"], "hood and mask follow cloth and accent"
    assert look.melee == "sword", "a class strikes with what it carries unless it says otherwise"


def test_a_bad_looks_file_lists_every_problem_at_once(tmp_path):
    path = write_looks(tmp_path, {
        "Bad Key": {},
        "knight": {"head": "crown", "weapon": "banana", "offhand": "kite", "body": "tabard", "tail": "long", "ranged": "laser",
                   "colors": {"cloth": "red", "paint": "#ffffff"}, "down_cap": "blue"},
    })
    with pytest.raises(HeroArtError) as error:
        read_looks(path)
    text = str(error.value)
    for expected in ("Bad Key: a class key", "head 'crown' is not one of", "weapon 'banana' is not one of", "'tail' is not a field",
                     "ranged 'laser'", "color cloth is 'red', not #rrggbb", "'paint' is not a color role", "down_cap is 'blue'"):
        assert expected in text, expected


@pytest.mark.parametrize("content", ["not json", "[]", "{}"])
def test_a_looks_file_that_is_not_an_object_of_classes_is_refused(tmp_path, content):
    path = tmp_path / "looks.json"
    path.write_text(content)
    with pytest.raises(HeroArtError):
        read_looks(path)


def test_the_command_line_draws_every_class_or_just_the_ones_named(tmp_path, capsys):
    path = write_looks(tmp_path)
    assert main([str(path), "--out", str(tmp_path / "all")]) == 0
    assert sorted(file.name for file in (tmp_path / "all").glob("*_fight.sheet.json")) == ["fighter_fight.sheet.json", "monk_fight.sheet.json", "scout_fight.sheet.json"]
    assert main([str(path), "monk", "--out", str(tmp_path / "one")]) == 0
    assert sorted(file.name for file in (tmp_path / "one").glob("*.sheet.json")) == ["monk_fight.sheet.json", "monk_map.sheet.json"]
    capsys.readouterr()
    assert main([str(path), "wizard", "--out", str(tmp_path / "none")]) == 1
    assert "no look for wizard" in capsys.readouterr().err
    assert not (tmp_path / "none").exists()
    assert main([str(tmp_path / "missing.json"), "--out", str(tmp_path / "none")]) == 1


def test_the_readme_names_every_piece_a_look_can_use():
    import re
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("**Making hero art.**") :].split("\n", 1)[0]
    for table in (HEADS, CARRY, OFFHANDS, BODIES):
        for name in table:
            assert re.search(rf"\b{name}\b", section), f"{name} is a piece, so the README's Making hero art says so"

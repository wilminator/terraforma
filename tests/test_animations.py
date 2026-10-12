"""The animation file (``name.anim.json``): what a sheet's named animations may say, checked when the server starts."""

import json

import pytest

from terraforma.animations import AnimationError, check_animation_folder, check_animations
from terraforma.client import ClientError, check_game_client
from terraforma.example import GAME as EXAMPLE
from terraforma.game import Game


def file_of(**animations):
    return {"animations": animations}


BASE = {"frames": [0, 1]}


def test_an_animation_is_a_list_of_frames_and_everything_else_has_a_default():
    checked = check_animations(file_of(base={"frames": [0, {"frame": 1, "ms": 250}]}))
    base = checked.animations["base"]
    assert base.ms == 100 and base.loop is True and base.sync == "shared"
    assert [(each.frame, each.ms) for each in base.frames] == [(0, None), (1, 250)], "a bare number is a frame with the animation's time"
    assert check_animations(file_of(base={"frames": [0], "ms": 40, "loop": False, "sync": "random"})).animations["base"].sync == "random"


def test_a_frame_and_an_alias_may_flip_scale_rotate_and_name_a_pivot():
    checked = check_animations(
        {
            "animations": {"stand_right": {"frames": [{"frame": 0, "flip_y": True, "scale": 2, "rotate": -90, "pivot": [4, 5.5]}]}},
            "aliases": {"stand_left": {"of": "stand_right", "flip_x": True}},
        }
    )
    frame = checked.animations["stand_right"].frames[0]
    assert (frame.flip_y, frame.scale, frame.rotate, frame.pivot) == (True, 2, -90, [4, 5.5])
    assert checked.aliases["stand_left"].flip_x is True and checked.aliases["stand_left"].pivot is None


def test_an_alias_may_point_at_another_alias():
    chain = {"animations": {"a": BASE}, "aliases": {"b": {"of": "a"}, "c": {"of": "b", "rotate": 90}}}
    assert set(check_animations(chain).aliases) == {"b", "c"}


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({}, "animations"),
        ({"animations": {}}, "at least one"),
        (file_of(base={"frames": []}), "frames"),
        (file_of(base={"frames": [-1]}), "frames"),
        (file_of(base={"frames": [0], "ms": 0}), "ms"),
        (file_of(base={"frames": [0], "sync": "together"}), "sync"),
        (file_of(base={"frames": [0], "speed": 2}), "speed"),
        (file_of(base={"frames": [{"frame": 0, "scale": 0}]}), "scale"),
        (file_of(base={"frames": [{"frame": 0, "rotate": 400}]}), "rotate"),
        (file_of(base={"frames": [{"frame": 0, "pivot": [1]}]}), "pivot"),
        (file_of(**{"Base": BASE}), "name"),
        (file_of(**{"../base": BASE}), "name"),
        ({"animations": {"a": BASE}, "aliases": {"b": {"of": "missing"}}}, "'missing'"),
        ({"animations": {"a": BASE}, "aliases": {"a": {"of": "a"}}}, "both"),
        ({"animations": {"a": BASE}, "aliases": {"b": {"of": "c"}, "c": {"of": "b"}}}, "loop"),
        ({"animations": {"a": BASE}, "aliases": {"b": {"of": "b"}}}, "loop"),
    ],
)
def test_a_bad_animation_file_is_refused_and_says_what_is_wrong(data, problem):
    with pytest.raises(AnimationError, match=problem):
        check_animations(data)


def test_frames_the_sheet_does_not_have_are_refused_when_the_sheet_is_known():
    check_animations(file_of(base={"frames": [0, 1]}), frames=2)
    with pytest.raises(AnimationError, match="frame 2"):
        check_animations(file_of(base={"frames": [0, 2]}), frames=2)


def test_a_folder_of_animation_files_is_checked_and_the_bad_one_is_named(tmp_path):
    (tmp_path / "good.anim.json").write_text(json.dumps(file_of(base=BASE)))
    (tmp_path / "other.json").write_text("not even json")  # (not an animation file: none of this check's business)
    check_animation_folder(tmp_path)
    (tmp_path / "bad.anim.json").write_text(json.dumps(file_of(base={"frames": []})))
    with pytest.raises(AnimationError, match="bad.anim.json"):
        check_animation_folder(tmp_path)
    (tmp_path / "bad.anim.json").write_text("{")
    with pytest.raises(AnimationError, match="bad.anim.json: not JSON"):
        check_animation_folder(tmp_path)


def test_the_server_will_not_start_with_a_bad_animation_file(tmp_path):
    (tmp_path / "bad.anim.json").write_text(json.dumps({"animations": {"a": BASE}, "aliases": {"b": {"of": "b"}}}))
    with pytest.raises(ClientError, match="bad.anim.json"):
        check_game_client(Game(name="Bad", assets_dir=tmp_path))


def test_the_example_games_animation_files_are_valid_and_fit_their_sheets():
    for path in sorted((EXAMPLE.assets_dir).glob("*.anim.json")):
        sheet = json.loads((EXAMPLE.assets_dir / (path.name.removesuffix(".anim.json") + ".sheet.json")).read_text())
        check_animations(json.loads(path.read_text()), frames=sheet["frames"])
    assert (EXAMPLE.assets_dir / "crystal.anim.json").is_file()

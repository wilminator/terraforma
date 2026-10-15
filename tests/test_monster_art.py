"""`utils/monster_art.py` draws extra monster bodies for games: valid sheets and animations, the same every time (no database)."""

import importlib.util
import json
from pathlib import Path

from terraforma.animations import check_animation_folder

SCRIPT = Path(__file__).parent.parent / "utils" / "monster_art.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("monster_art", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_draws_every_body_as_a_four_pose_sheet_with_valid_animations(tmp_path):
    names = load_tool().draw(tmp_path, "vt_")
    assert names == ["vt_brute", "vt_mage", "vt_ghost", "vt_robot", "vt_drone"]
    for name in names:
        sheet = json.loads((tmp_path / f"{name}.sheet.json").read_text())
        assert sheet["frames"] == 4 and sheet["frame"] == [32, 32] and sheet["alpha"] is True
        assert (tmp_path / f"{name}.png").exists() and (tmp_path / f"{name}.alpha.png").exists()
        assert {"base", "hit", "die", "attack_close"} <= set(json.loads((tmp_path / f"{name}.anim.json").read_text())["animations"])
    check_animation_folder(tmp_path)


def test_it_draws_the_same_files_every_time(tmp_path):
    tool = load_tool()
    tool.draw(tmp_path / "one")
    tool.draw(tmp_path / "two")
    for path in sorted((tmp_path / "one").iterdir()):
        assert path.read_bytes() == (tmp_path / "two" / path.name).read_bytes(), path.name


def test_it_leaves_the_example_games_own_sheets_alone(tmp_path):
    load_tool().draw(tmp_path)
    assert not {path.name for path in tmp_path.iterdir()} & {f"{name}.png" for name in ("slime", "bat", "wolf", "goblin", "skeleton", "imp", "hero_fight")}

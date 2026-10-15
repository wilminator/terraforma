"""Draws extra monster bodies for games: a brute, a mage, a ghost, a robot and a drone, with no dependencies beyond the engine.

    PYTHONPATH=src python3 utils/monster_art.py --out SOME/ASSETS/FOLDER [--prefix vt_]

Like ``example_art.py`` (whose canvas, sheet writer and animation files it reuses), the pictures are made from the pixel rules
below (flat shapes, one outline), not taken from any other work, and are the engine's own: AGPL-3.0 with the module permission.
Run it again to redraw them; the output is the same every time. A game keeps the files it wants in its own assets folder and
recolors each body with color maps (``terraforma.tools.colormap``), the way the example game does. Every body faces left, as a
fight shows monsters, and has the four poses of a monster fight sheet: ``idle_a``, ``idle_b``, ``attack`` and ``hit``.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import example_art as art  # noqa: E402
from example_art import Canvas  # noqa: E402

POSES = ("idle_a", "idle_b", "attack", "hit")

#: Colors the bodies use besides example_art's: a brute's hide, a ghost's mist and a machine's plates and lens.
art.COLORS |= {
    "brute": "#8a9a60", "brute_d": "#5a6a38", "ghost": "#c8e0f8", "ghost_l": "#f0f8ff", "ghost_d": "#88a8d0",
    "metal": "#8890a0", "metal_d": "#505868", "metal_l": "#c0c8d8", "lens": "#d03030",
}


def brute(pose):
    """A hulking club-carrier: broad shoulders, small head, tusks."""
    c = Canvas()
    bob = -1 if pose == "idle_b" else 0
    lean = 3 if pose == "hit" else 0
    reach = -3 if pose == "attack" else 0
    c.rect(11 + lean, 24, 14 + lean, 29, "brute_d")  # legs
    c.rect(17 + lean, 24, 20 + lean, 29, "brute_d")
    c.rect(10 + lean, 29, 14 + lean, 30, "rag_d")
    c.rect(17 + lean, 29, 21 + lean, 30, "rag_d")
    c.ellipse(15.5 + lean, 19 + bob, 9, 7, "brute")  # barrel chest
    c.rect(8 + lean, 21 + bob, 23 + lean, 23 + bob, "rag")  # loincloth belt
    c.ellipse(15.5 + lean, 9 + bob, 4.5, 4, "brute")  # head
    c.rect(11 + lean, 10 + bob, 12 + lean, 11 + bob, "fang")  # tusks
    c.rect(19 + lean, 10 + bob, 20 + lean, 11 + bob, "fang")
    c.px(13 + lean, 8 + bob, "red")
    c.px(18 + lean, 8 + bob, "red")
    c.rect(5 + lean, 14 + bob, 8 + lean, 22 + bob, "brute")  # arms
    c.rect(23 + lean, 14 + bob, 26 + lean, 22 + bob, "brute")
    if pose == "attack":
        c.rect(2, 7, 4, 17, "wood")  # club raised and swung forward
        c.rect(0, 3, 6, 8, "rag_d")
    elif pose == "hit":
        c.rect(26, 21, 27, 28, "wood")
        c.rect(25, 27, 29, 30, "rag_d")
    else:
        c.rect(3, 15 + bob, 4, 27, "wood")
        c.rect(2, 24, 6, 28, "rag_d")
    return c.outlined()


def mage(pose):
    """A robed caster with a pointed hat and a staff that holds a glowing orb."""
    c = Canvas()
    bob = -1 if pose == "idle_b" else 0
    lean = 2 if pose == "hit" else 0
    c.triangle((15 + lean, 12 + bob), (6 + lean, 29), (25 + lean, 29), "cloth")  # robe
    c.rect(9 + lean, 27, 22 + lean, 29, "cloth_d")
    c.rect(14 + lean, 18 + bob, 17 + lean, 28, "trim")  # sash
    c.ellipse(15.5 + lean, 10 + bob, 4, 4, "skin")  # face
    c.triangle((10 + lean, 8 + bob), (15 + lean, -1 + bob), (21 + lean, 8 + bob), "cloth_d")  # hat
    c.rect(9 + lean, 7 + bob, 22 + lean, 8 + bob, "cloth_d")
    c.px(13 + lean, 10 + bob, "out")
    c.px(18 + lean, 10 + bob, "out")
    c.rect(11 + lean, 17 + bob, 13 + lean, 22 + bob, "cloth")  # sleeves
    if pose == "attack":
        c.rect(3, 13, 5, 28, "wood")  # staff thrust forward, orb flaring
        c.ellipse(4, 11, 3, 3, "ember")
        c.px(4, 11, "fang")
        c.rect(6, 17, 11, 19, "cloth")
    else:
        c.rect(5 + lean, 10 + bob, 6 + lean, 27, "wood")
        c.ellipse(5.5 + lean, 8 + bob, 2.5, 2.5, "glass")
        c.rect(8 + lean, 18 + bob, 11 + lean, 19 + bob, "cloth")
    return c.outlined()


def ghost(pose):
    """A floating wraith: a hooded shape with a ragged hem, hollow eyes and long arms."""
    c = Canvas()
    bob = -1 if pose == "idle_b" else 0
    hem = 0 if pose != "idle_b" else 1
    if pose == "hit":
        c.ellipse(16, 22, 9, 6, "ghost")
        c.rect(7, 26, 24, 27, "ghost_d")
        c.px(12, 21, "out")
        c.px(13, 20, "out")
        c.px(18, 21, "out")
        c.px(19, 20, "out")
        return c.outlined()
    c.ellipse(15.5, 12 + bob, 7, 7, "ghost")  # hood
    c.rect(8, 12 + bob, 23, 25 + bob, "ghost")  # body
    for step, x in enumerate(range(8, 24, 4)):  # ragged hem, swapping each frame
        low = 28 if (step + hem) % 2 == 0 else 26
        c.rect(x, 25 + bob, x + 3, low + bob, "ghost_d")
    c.ellipse(15.5, 12 + bob, 4.5, 4.5, "ghost_d")  # the dark of the hood
    c.rect(12, 11 + bob, 13, 13 + bob, "ghost_l")
    c.rect(18, 11 + bob, 19, 13 + bob, "ghost_l")
    if pose == "attack":
        c.rect(13, 15, 18, 17, "out")  # a wide wail
        c.rect(1, 14, 8, 15, "ghost")  # arms stretched out
        c.rect(1, 19, 8, 20, "ghost")
    else:
        c.rect(4, 16 + bob, 8, 17 + bob, "ghost")
        c.rect(4, 17 + bob, 5, 21 + bob, "ghost")
    return c.outlined()


def robot(pose):
    """A heavy humanoid machine: boxy plates, a visor with a red lens and an arm cannon."""
    c = Canvas()
    bob = -1 if pose == "idle_b" else 0
    lean = 2 if pose == "hit" else 0
    c.rect(11 + lean, 23, 14 + lean, 29, "metal_d")  # legs
    c.rect(17 + lean, 23, 20 + lean, 29, "metal_d")
    c.rect(10 + lean, 29, 15 + lean, 30, "metal")
    c.rect(16 + lean, 29, 21 + lean, 30, "metal")
    c.rect(9 + lean, 13 + bob, 22 + lean, 23 + bob, "metal")  # torso
    c.rect(11 + lean, 15 + bob, 20 + lean, 17 + bob, "metal_l")
    c.rect(14 + lean, 19 + bob, 17 + lean, 21 + bob, "lens")  # core
    c.rect(11 + lean, 5 + bob, 20 + lean, 12 + bob, "metal")  # head
    c.rect(10 + lean, 7 + bob, 15 + lean, 9 + bob, "glass")  # visor, to the front
    c.px(12 + lean, 8 + bob, "lens")
    c.line(21 + lean, 5 + bob, 22 + lean, 1 + bob, "metal_d")  # antenna
    c.rect(22 + lean, 14 + bob, 25 + lean, 22 + bob, "metal_d")  # back arm
    if pose == "attack":
        c.rect(0, 15, 9, 18, "metal_d")  # arm cannon, firing
        c.rect(0, 14, 2, 19, "ember")
    else:
        c.rect(5 + lean, 14 + bob, 9 + lean, 21 + bob, "metal_d")
        c.rect(3 + lean, 20 + bob, 8 + lean, 23 + bob, "metal")
    return c.outlined()


def drone(pose):
    """A hovering sphere with one lens, a spinning rotor and a gun pod."""
    c = Canvas()
    bob = {"idle_a": 0, "idle_b": 1, "attack": 0, "hit": 3}[pose]
    tilt = 2 if pose == "hit" else 0
    c.ellipse(16, 17 + bob, 9, 8, "metal")
    c.ellipse(15, 15 + bob, 5, 3, "metal_l")
    c.rect(7, 22 + bob, 24, 23 + bob, "metal_d")
    c.ellipse(11, 17 + bob, 3.5, 3.5, "out")  # the lens
    c.ellipse(11, 17 + bob, 2, 2, "lens")
    c.px(10, 16 + bob, "fang")
    rotor = 7 if pose != "idle_b" else 11  # the blades alternate length
    c.rect(16 - rotor, 7 + bob + tilt, 16 + rotor, 8 + bob + tilt, "metal_l")
    c.rect(15, 8 + bob, 16, 10 + bob, "metal_d")
    c.rect(24, 17 + bob, 28, 20 + bob, "metal_d")  # gun pod at the back
    if pose == "attack":
        c.line(0, 17, 6, 17, "ember")
        c.line(0, 18, 6, 18, "ember")
    if pose == "hit":
        c.px(20, 8, "ember")
        c.px(22, 6, "metal_d")
        c.px(18, 5, "metal_d")
    return c.outlined()


BODIES = {"brute": brute, "mage": mage, "ghost": ghost, "robot": robot, "drone": drone}


def draw(out: Path, prefix: str = "") -> list[str]:
    """Writes every body's sheet (``<prefix><name>.png`` and ``.alpha.png``, ``.sheet.json``) and animation file into $out."""
    out.mkdir(parents=True, exist_ok=True)
    art.ASSETS = out
    names = []
    for name, pose_of in BODIES.items():
        art.write_sheet(prefix + name, [pose_of(pose) for pose in POSES], 4)
        art.write_json(f"{prefix}{name}.anim.json", art.fight_animations(POSES, extra_names=False))
        names.append(prefix + name)
    return names


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Draw the extra monster bodies into a game's assets folder.")
    parser.add_argument("--out", type=Path, required=True, help="the assets folder to write into")
    parser.add_argument("--prefix", default="", help="put in front of every file name, so they never clash with a game's own")
    arguments = parser.parse_args(argv)
    names = draw(arguments.out, arguments.prefix)
    print("drew", ", ".join(names), "into", arguments.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

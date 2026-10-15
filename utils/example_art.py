"""Draws the example game's placeholder art: sheets, color maps and animation files, with no dependencies beyond the engine.

    PYTHONPATH=src python3 utils/example_art.py

The pictures are made from the pixel rules below (flat shapes, one outline), not taken from any other work, and are
the engine's own: AGPL-3.0 with the module permission. Run it again to redraw them; the output is the same every time.
It writes into ``src/terraforma/example/assets`` the sheets (``name.png`` and ``name.alpha.png``), their ``name.sheet.json``,
the animations (``name.anim.json``) and the color maps (``name.colors.json``) that ``ASSETS.md`` lists.
"""

import json
import sys
from pathlib import Path

from terraforma.tools.qbtiles import png

ASSETS = Path(__file__).resolve().parent.parent / "src" / "terraforma" / "example" / "assets"
SIDE = 32

COLORS = {
    "out": "#202028", "skin": "#e8b890", "skin_d": "#c08860", "hair": "#704020", "cloth": "#3050a0", "cloth_d": "#203070",
    "trim": "#e0c050", "steel": "#c0c8d0", "steel_d": "#808890", "boot": "#503018", "glass": "#80d0e0", "wood": "#a06838",
    "slime": "#58c05a", "slime_l": "#98f090", "slime_d": "#2c8030", "bat": "#5a4a7a", "bat_l": "#8a76b0", "bat_w": "#3c2e58",
    "fang": "#f4f0e0", "goblin": "#78a840", "goblin_d": "#4c7828", "rag": "#8a5a32", "rag_d": "#5e3c20", "bone": "#ece4cc",
    "bone_d": "#b0a888", "rust": "#a05030", "fur": "#8a8a94", "fur_l": "#b8b8c4", "fur_d": "#5a5a66", "imp": "#c83c32",
    "imp_l": "#f07058", "imp_d": "#8c2020", "horn": "#f0d890", "ember": "#ffb030", "red": "#d03030",
}


class Canvas:
    """One frame: rows of color names, None where it is transparent."""

    def __init__(self):
        self.rows = [[None] * SIDE for _ in range(SIDE)]

    def px(self, x, y, color):
        if 0 <= x < SIDE and 0 <= y < SIDE:
            self.rows[y][x] = color

    def rect(self, x0, y0, x1, y1, color):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.px(x, y, color)

    def ellipse(self, cx, cy, rx, ry, color):
        for y in range(SIDE):
            for x in range(SIDE):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    self.px(x, y, color)

    def triangle(self, a, b, c, color):
        (ax, ay), (bx, by), (cx, cy) = a, b, c
        area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if area == 0:
            return
        for y in range(SIDE):
            for x in range(SIDE):
                w0 = ((bx - x) * (cy - y) - (cx - x) * (by - y)) / area
                w1 = ((cx - x) * (ay - y) - (ax - x) * (cy - y)) / area
                if w0 >= 0 and w1 >= 0 and w0 + w1 <= 1:
                    self.px(x, y, color)

    def line(self, x0, y0, x1, y1, color):
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for step in range(steps + 1):
            self.px(round(x0 + (x1 - x0) * step / steps), round(y0 + (y1 - y0) * step / steps), color)

    def outlined(self):
        """The frame with a one-pixel outline around everything drawn."""
        out = Canvas()
        out.rows = [row[:] for row in self.rows]
        for y in range(SIDE):
            for x in range(SIDE):
                if self.rows[y][x] is None and any(
                    0 <= x + dx < SIDE and 0 <= y + dy < SIDE and self.rows[y + dy][x + dx] is not None
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                ):
                    out.rows[y][x] = "out"
        return out

    def mirrored(self):
        out = Canvas()
        out.rows = [row[::-1] for row in self.rows]
        return out


# --- a person: heroes, goblins, skeletons and imps share one body --------------------------------------------------

HERO = dict(skin="skin", hair="hair", cloth="cloth", cloth_d="cloth_d", trim="trim", boot="boot", weapon="sword", extra=None)


def person_back(pose, look):
    """A standing figure seen from behind (the fight shows a player's own side this way), 32 by 32. $pose is back_a, back_b, back_attack or back_hit."""
    c = Canvas()
    skin, hair, cloth, cloth_d, trim, boot = (look[name] for name in ("skin", "hair", "cloth", "cloth_d", "trim", "boot"))
    lean = -2 if pose == "back_hit" else 0
    bob = -1 if pose == "back_b" else 0
    c.rect(12 + lean, 22, 14 + lean, 28, cloth_d)  # legs
    c.rect(17 + lean, 22, 19 + lean, 28, cloth_d)
    c.rect(11 + lean, 28, 14 + lean, 29, boot)
    c.rect(17 + lean, 28, 20 + lean, 29, boot)
    c.rect(11 + lean, 14 + bob, 20 + lean, 22, cloth)  # back, belt
    c.rect(11 + lean, 20 + bob, 20 + lean, 20 + bob, trim)
    c.ellipse(15.5 + lean, 9 + bob, 5, 5.5, hair or skin)  # the back of the head: hair, or bare
    if pose == "back_attack":
        c.rect(8, 15, 10, 21, cloth)
        c.rect(21, 7, 23, 15, cloth)  # the sword arm goes up and over
        c.rect(22, 5, 23, 6, skin)
        if look["weapon"]:
            _weapon(c, look["weapon"], 22, 5, "forward")
    else:
        c.rect(8 + lean, 15 + bob, 10 + lean, 21 + bob, cloth)
        c.rect(21 + lean, 15 + bob, 23 + lean, 21 + bob, cloth)
        c.rect(8 + lean, 22 + bob, 9 + lean, 23 + bob, skin)
        c.rect(22 + lean, 22 + bob, 23 + lean, 23 + bob, skin)
    if look["extra"]:
        look["extra"](c, "idle_a", lean, bob)
    return c.outlined()


def person_fight(pose, look):
    """A standing figure facing right, 32 by 32. $pose is idle_a, idle_b, attack, hit, down, cast, item or shoot."""
    if pose.startswith("back_"):
        return person_back(pose, look)
    c = Canvas()
    skin, hair, cloth, cloth_d, trim, boot = (look[name] for name in ("skin", "hair", "cloth", "cloth_d", "trim", "boot"))
    lean = -2 if pose == "hit" else 0
    bob = -1 if pose == "idle_b" else 0
    if pose == "down":
        c.rect(5, 25, 22, 29, cloth)
        c.rect(5, 28, 22, 29, cloth_d)
        c.rect(22, 26, 27, 29, boot)
        c.ellipse(5, 26.5, 4, 3.5, skin)
        if hair:
            c.rect(2, 23, 5, 25, hair)
        c.px(5, 26, "out")
        if look["extra"]:
            look["extra"](c, pose, 0, 0)
        return c.outlined()
    c.rect(12 + lean, 22, 14 + lean, 28, cloth_d)  # legs
    c.rect(17 + lean, 22, 19 + lean, 28, cloth_d)
    c.rect(11 + lean, 28, 14 + lean, 29, boot)
    c.rect(17 + lean, 28, 20 + lean, 29, boot)
    c.rect(11 + lean, 14 + bob, 20 + lean, 22, cloth)  # torso, belt
    c.rect(11 + lean, 20 + bob, 20 + lean, 20 + bob, trim)
    c.ellipse(15.5 + lean, 9 + bob, 5, 5.5, skin)  # head
    if hair:
        c.rect(10 + lean, 3 + bob, 21 + lean, 6 + bob, hair)
        c.rect(10 + lean, 6 + bob, 11 + lean, 9 + bob, hair)
    c.px(18 + lean, 9 + bob, "out")
    c.px(19 + lean, 9 + bob, "out")
    if pose == "hit":
        c.px(18 + lean, 8 + bob, "out")
    arm = lambda x0, y0, x1, y1: c.rect(x0 + lean, y0 + bob, x1 + lean, y1 + bob, cloth)  # noqa: E731
    hand = lambda x, y: c.rect(x + lean, y + bob, x + 1 + lean, y + 1 + bob, skin)  # noqa: E731
    weapon = look["weapon"]
    if pose in ("idle_a", "idle_b", "hit"):
        arm(8, 15, 10, 21)
        arm(21, 15, 23, 21)
        hand(8, 22)
        hand(22, 22)
        if weapon and pose != "hit":
            _weapon(c, weapon, 23, 22, "down")
    elif pose == "attack":
        arm(8, 15, 10, 21)
        arm(21, 15, 28, 17)
        hand(28, 15)
        if weapon:
            _weapon(c, weapon, 29, 15, "forward")
    elif pose == "cast":
        arm(7, 8, 9, 15)
        arm(22, 8, 24, 15)
        hand(7, 6)
        hand(23, 6)
        c.px(8, 4, "ember")
        c.px(24, 4, "ember")
    elif pose == "item":
        arm(8, 15, 10, 21)
        arm(21, 10, 23, 16)
        hand(22, 8)
        c.rect(22, 3, 24, 6, "glass")
        c.px(23, 2, "wood")
    elif pose == "shoot":
        arm(8, 15, 10, 21)
        arm(21, 13, 27, 15)
        hand(27, 13)
        c.line(29, 8, 29, 20, "wood")
        c.line(29, 8, 27, 14, "steel_d")
        c.line(29, 20, 27, 14, "steel_d")
    if look["extra"]:
        look["extra"](c, pose, lean, bob)
    return c.outlined()


def _weapon(c, kind, x, y, how):
    if kind == "sword":
        if how == "down":
            c.rect(x, y - 1, x + 1, y + 5, "steel")
            c.rect(x - 1, y - 1, x + 2, y - 1, "trim")
        else:
            c.rect(x, y, x + 2, y + 1, "steel")
            c.line(x, y - 1, x + 2, y - 7, "steel")
            c.line(x + 1, y - 1, x + 3, y - 7, "steel")
    elif kind == "club":
        if how == "down":
            c.rect(x, y - 2, x + 1, y + 4, "wood")
            c.rect(x - 1, y + 3, x + 2, y + 5, "rag_d")
        else:
            c.rect(x, y, x + 1, y + 1, "wood")
            c.rect(x, y - 6, x + 2, y - 1, "wood")
            c.rect(x - 1, y - 8, x + 3, y - 5, "rag_d")
    elif kind == "blade":
        if how == "down":
            c.rect(x, y - 1, x + 1, y + 6, "rust")
        else:
            c.line(x, y - 1, x + 2, y - 8, "rust")
            c.line(x + 1, y - 1, x + 3, y - 8, "rust")


def goblin_ears(c, pose, lean, bob):
    if pose == "down":
        return
    c.triangle((10 + lean, 8 + bob), (4 + lean, 5 + bob), (10 + lean, 11 + bob), "goblin")
    c.triangle((21 + lean, 8 + bob), (27 + lean, 5 + bob), (21 + lean, 11 + bob), "goblin")


def skeleton_ribs(c, pose, lean, bob):
    if pose != "down":
        for y in (16, 18, 20):
            c.rect(12 + lean, y + bob, 19 + lean, y + bob, "bone_d")
        c.rect(15 + lean, 14 + bob, 16 + lean, 22 + bob, "bone_d")


def imp_extras(c, pose, lean, bob):
    if pose == "down":
        return
    c.triangle((11 + lean, 4 + bob), (8 + lean, -1 + bob), (13 + lean, 3 + bob), "horn")
    c.triangle((20 + lean, 4 + bob), (23 + lean, -1 + bob), (18 + lean, 3 + bob), "horn")
    c.triangle((11 + lean, 16 + bob), (3 + lean, 10 + bob), (6 + lean, 21 + bob), "imp_d")  # wing
    c.line(10 + lean, 24, 6 + lean, 27, "imp_d")  # tail


GOBLIN = dict(skin="goblin", hair=None, cloth="rag", cloth_d="rag_d", trim="goblin_d", boot="rag_d", weapon="club", extra=goblin_ears)
SKELETON = dict(skin="bone", hair=None, cloth="bone", cloth_d="bone_d", trim="bone_d", boot="bone_d", weapon="blade", extra=skeleton_ribs)
IMP = dict(skin="imp", hair=None, cloth="imp", cloth_d="imp_d", trim="imp_l", boot="imp_d", weapon=None, extra=imp_extras)

FIGHT_POSES = ("idle_a", "idle_b", "attack", "hit", "down", "cast", "item", "shoot")
BACK_POSES = ("back_a", "back_b", "back_attack", "back_hit")  # (the hero's back view: appended, so the frames above keep their numbers)


def person_sheet(look, poses, facing_left):
    frames = [person_fight(pose, look) for pose in poses]
    return [frame.mirrored() for frame in frames] if facing_left else frames


# --- a walking figure for the map ---------------------------------------------------------------------------------------

def person_map(facing, step):
    """A 32 by 32 walking figure; $facing is down, up or right (left is the right one flipped by an alias); $step 0 stands, 1 and 2 stride."""
    c = Canvas()
    left_up = 3 if step == 1 else 0
    right_up = 3 if step == 2 else 0
    bob = -1 if step else 0
    side = facing == "right"
    if side:
        c.rect(14, 23, 17, 28 - left_up, "cloth_d")
        c.rect(15, 23, 18, 28 - right_up, "cloth_d")
        c.rect(14, 28 - left_up, 18, 29 - left_up, "boot")
        c.rect(13, 14 + bob, 19, 23, "cloth")
        c.rect(13, 21 + bob, 19, 21 + bob, "trim")
        c.ellipse(16, 8.5 + bob, 5, 5.5, "skin")
        c.rect(11, 3 + bob, 20, 6 + bob, "hair")
        c.rect(11, 6 + bob, 14, 11 + bob, "hair")
        c.px(19, 9 + bob, "out")
        arm_swing = 2 if step == 1 else -2 if step == 2 else 0
        c.rect(15 + arm_swing, 15 + bob, 17 + arm_swing, 21 + bob, "cloth_d")
        c.rect(15 + arm_swing, 22 + bob, 17 + arm_swing, 23 + bob, "skin")
    else:
        c.rect(12, 23, 14, 28 - left_up, "cloth_d")
        c.rect(17, 23, 19, 28 - right_up, "cloth_d")
        c.rect(11, 28 - left_up, 14, 29 - left_up, "boot")
        c.rect(17, 28 - right_up, 20, 29 - right_up, "boot")
        c.rect(11, 14 + bob, 20, 23, "cloth")
        c.rect(11, 21 + bob, 20, 21 + bob, "trim")
        c.rect(8, 15 + bob, 10, 21 + bob + (1 if step == 2 else 0), "cloth")
        c.rect(21, 15 + bob, 23, 21 + bob + (1 if step == 1 else 0), "cloth")
        c.rect(8, 22 + bob, 9, 23 + bob, "skin")
        c.rect(22, 22 + bob, 23, 23 + bob, "skin")
        c.ellipse(15.5, 8.5 + bob, 5, 5.5, "skin")
        if facing == "down":
            c.rect(10, 3 + bob, 21, 6 + bob, "hair")
            c.rect(10, 6 + bob, 11, 9 + bob, "hair")
            c.rect(20, 6 + bob, 21, 9 + bob, "hair")
            c.px(13, 9 + bob, "out")
            c.px(18, 9 + bob, "out")
        else:
            c.ellipse(15.5, 8 + bob, 5.2, 5.6, "hair")
    return c.outlined()


# --- the animals --------------------------------------------------------------------------------------------------------

def slime(pose):
    c = Canvas()
    if pose == "idle_a":
        c.ellipse(16, 22, 12, 8, "slime")
        c.ellipse(11, 18, 3, 2, "slime_l")
        eyes = (12, 20)
    elif pose == "idle_b":
        c.ellipse(16, 23, 13, 7, "slime")
        c.ellipse(11, 20, 3, 1.5, "slime_l")
        eyes = (12, 22)
    elif pose == "attack":
        c.ellipse(14, 20, 10, 11, "slime")
        c.ellipse(10, 14, 3, 3, "slime_l")
        eyes = (9, 17)
    else:
        c.ellipse(16, 26, 14, 4.5, "slime")
        eyes = None
    c.rect(*((3, 28, 28, 29) if pose == "hit" else (4, 27, 27, 28)), "slime_d")
    if eyes:
        x, y = eyes
        c.rect(x, y, x + 1, y + 2, "out")
        c.rect(x + 7, y, x + 8, y + 2, "out")
    else:
        c.rect(10, 25, 14, 25, "out")
        c.rect(18, 25, 22, 25, "out")
    return c.outlined()


def bat(pose):
    c = Canvas()
    wings = {"idle_a": 0, "idle_b": 1, "attack": 2, "hit": 3}[pose]
    if wings == 0:
        c.triangle((13, 15), (1, 4), (5, 18), "bat_w")
        c.triangle((18, 15), (30, 4), (26, 18), "bat_w")
    elif wings == 1:
        c.triangle((13, 17), (2, 25), (4, 13), "bat_w")
        c.triangle((18, 17), (29, 25), (27, 13), "bat_w")
    elif wings == 2:
        c.triangle((13, 15), (0, 13), (3, 22), "bat_w")
        c.triangle((18, 15), (31, 13), (28, 22), "bat_w")
    else:
        c.triangle((13, 16), (8, 26), (12, 26), "bat_w")
        c.triangle((18, 16), (23, 26), (19, 26), "bat_w")
    c.ellipse(15.5, 17, 4, 6, "bat")
    c.ellipse(15.5, 17, 2, 4, "bat_l")
    c.ellipse(15.5, 10, 4.5, 4, "bat")
    c.triangle((11, 8), (10, 2), (14, 7), "bat")
    c.triangle((20, 8), (21, 2), (17, 7), "bat")
    c.px(13, 10, "red")
    c.px(18, 10, "red")
    if pose == "attack":
        c.px(14, 13, "fang")
        c.px(17, 13, "fang")
    return c.outlined()


def wolf(pose):
    c = Canvas()
    shift = {"idle_a": 0, "idle_b": 0, "attack": -3, "hit": 2}[pose]
    if pose == "hit":
        c.ellipse(18, 21, 9, 4.5, "fur")
        c.ellipse(10, 20, 4, 3.5, "fur")
        c.rect(5, 20, 7, 21, "fur_l")
        c.rect(12, 24, 14, 28, "fur_d")
        c.rect(21, 24, 23, 28, "fur_d")
        c.triangle((26, 18), (31, 14), (28, 22), "fur_d")
        c.px(9, 19, "out")
        return c.outlined()
    c.ellipse(17 + shift, 19, 9, 5, "fur")
    c.ellipse(17 + shift, 21, 7, 2, "fur_l")
    c.ellipse(7 + shift, 14, 4.5, 4, "fur")
    c.rect(1 + shift, 14, 5 + shift, 17, "fur_l")
    c.triangle((6 + shift, 11), (7 + shift, 6), (10 + shift, 11), "fur_d")
    c.px(6 + shift, 13, "red")
    legs = ((10, 0), (13, 1), (21, 1), (24, 0)) if pose != "idle_b" else ((10, 1), (13, 0), (21, 0), (24, 1))
    for x, up in legs:
        c.rect(x + shift, 23, x + 1 + shift, 28 - up, "fur_d")
    c.triangle((25 + shift, 17), (31, 10 if pose == "idle_a" else 12), (26 + shift, 21), "fur_d")
    if pose == "attack":
        c.rect(1 + shift, 17, 5 + shift, 18, "red")
        c.px(2 + shift, 17, "fang")
        c.px(4 + shift, 17, "fang")
    return c.outlined()


# --- sheets, animations and color maps -------------------------------------------------------------------------------------

def write_sheet(name, frames, columns):
    colors = []
    for frame in frames:
        for row in frame.rows:
            for color in row:
                if color is not None and color not in colors:
                    colors.append(color)
    rows_count = -(-len(frames) // columns)
    width, height = columns * SIDE, rows_count * SIDE
    color_rows = [bytearray(width) for _ in range(height)]
    alpha_rows = [bytearray(width) for _ in range(height)]
    for number, frame in enumerate(frames):
        left, top = number % columns * SIDE, number // columns * SIDE
        for y in range(SIDE):
            for x in range(SIDE):
                color = frame.rows[y][x]
                if color is not None:
                    color_rows[top + y][left + x] = colors.index(color)
                    alpha_rows[top + y][left + x] = 255
    palette = [tuple(int(COLORS[color][at : at + 2], 16) for at in (1, 3, 5)) for color in colors]
    (ASSETS / f"{name}.png").write_bytes(png(width, height, [bytes(row) for row in color_rows], palette))
    (ASSETS / f"{name}.alpha.png").write_bytes(png(width, height, [bytes(row) for row in alpha_rows]))
    write_json(f"{name}.sheet.json", {
        "frame": [SIDE, SIDE], "grid": [columns, rows_count], "frames": len(frames),
        "palette": [COLORS[color] for color in colors], "alpha": True,
    })


def write_json(file_name, data):
    (ASSETS / file_name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def fight_animations(order, extra_names):
    """The animations of a fight sheet whose frames are $order (poses by name); $extra_names says if it can cast, use an item and shoot.
    A sheet with back poses also has the back view of its idle, hit, die and close attack (``_up``; the others fall back to the front)."""
    at = {pose: number for number, pose in enumerate(order)}
    animations = {
        "base": {"frames": [at["idle_a"], at["idle_b"]], "ms": 450},
        "hit": {"frames": [at["hit"], at["hit"], at["idle_a"]], "ms": 120, "loop": False},
        "die": {"frames": [at["hit"], {"frame": at["hit"], "rotate": 60}, at.get("down", at["hit"])], "ms": 180, "loop": False},
        "attack_close": {"frames": [{"frame": at["attack"], "ms": 260}, at["idle_a"]], "loop": False},
        "run": {"frames": [at["idle_a"], at["idle_b"]], "ms": 90},
    }
    aliases = {"flee": {"of": "run", "flip_x": True}}
    if "back_a" in at:
        animations |= {
            "base_up": {"frames": [at["back_a"], at["back_b"]], "ms": 450},
            "hit_up": {"frames": [at["back_hit"], at["back_hit"], at["back_a"]], "ms": 120, "loop": False},
            "attack_close_up": {"frames": [{"frame": at["back_attack"], "ms": 260}, at["back_a"]], "loop": False},
        }
        aliases["die_up"] = {"of": "die"}
    if extra_names:
        animations["spell"] = {"frames": [{"frame": at["cast"], "ms": 400}, at["idle_a"]], "loop": False}
        animations["item"] = {"frames": [{"frame": at["item"], "ms": 400}, at["idle_a"]], "loop": False}
        animations["attack_shoot"] = {"frames": [{"frame": at["shoot"], "ms": 300}, at["idle_a"]], "loop": False}
        aliases |= {
            "skill": {"of": "attack_close", "scale": 1.15},
            "attack_throw": {"of": "attack_shoot"},
            "equip": {"of": "item"},
        }
    else:
        aliases |= {"skill": {"of": "attack_close", "scale": 1.15}, "spell": {"of": "attack_close"}, "item": {"of": "base"}}
    return {"animations": animations, "aliases": aliases}


def map_animations():
    animations, aliases = {}, {}
    for number, facing in enumerate(("down", "up", "right")):
        stand, one, two = number * 3, number * 3 + 1, number * 3 + 2
        animations[f"stand_{facing}"] = {"frames": [stand]}
        animations[f"move_{facing}"] = {"frames": [one, stand, two, stand], "ms": 130}
    aliases["stand_left"] = {"of": "stand_right", "flip_x": True}
    aliases["move_left"] = {"of": "move_right", "flip_x": True}
    aliases["cast"] = {"of": "stand_down", "scale": 1.1}
    aliases["equip"] = {"of": "stand_down"}
    return {"animations": animations, "aliases": aliases}


HERO_COLOR_MAPS = {
    "hero_fighter": {"cloth": "#a03030", "cloth_d": "#701818"},
    "hero_scout": {"cloth": "#30803a", "cloth_d": "#1c5024"},
    "hero_mage": {"cloth": "#6040a0", "cloth_d": "#402870", "trim": "#f0f0ff"},
    "hero_cleric": {"cloth": "#e8e8f0", "cloth_d": "#a0a8c0", "hair": "#d8b050"},
}


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    hero_poses = (*FIGHT_POSES, *BACK_POSES)
    write_sheet("hero_fight", person_sheet(HERO, hero_poses, facing_left=False), 4)
    write_json("hero_fight.anim.json", fight_animations(hero_poses, extra_names=True))
    hero_map = [person_map(facing, step) for facing in ("down", "up", "right") for step in (0, 1, 2)]
    write_sheet("hero_map", hero_map, 5)
    write_json("hero_map.anim.json", map_animations())
    for name, swaps in HERO_COLOR_MAPS.items():
        write_json(f"{name}.colors.json", {COLORS[old]: new for old, new in swaps.items()})
    four = ("idle_a", "idle_b", "attack", "hit")
    sheets = {
        "slime": [slime(pose) for pose in four],
        "bat": [bat(pose) for pose in four],
        "wolf": [wolf(pose) for pose in four],
        "goblin": person_sheet(GOBLIN, (*four, "down"), facing_left=True),
        "skeleton": person_sheet(SKELETON, (*four, "down"), facing_left=True),
        "imp": person_sheet(IMP, (*four, "down"), facing_left=True),
    }
    for name, frames in sheets.items():
        write_sheet(name, frames, 5 if len(frames) == 5 else 4)
        write_json(f"{name}.anim.json", fight_animations((*four, "down") if len(frames) == 5 else four, extra_names=False))
    print("drew", len(list(ASSETS.glob("*.sheet.json"))), "sheets into", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

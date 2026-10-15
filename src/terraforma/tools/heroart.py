"""Draws a game's heroes, one fight sheet and one map sheet for each class, as tilesheets.

    python -m terraforma.tools.heroart looks.json --out assets/             # every class in the file
    python -m terraforma.tools.heroart looks.json knight monk --out assets/ # only these

The looks file says how each class is dressed: ``{"knight": {"colors": {"cloth": "#7e8aa0"}, "head": "helm", "weapon": "sword",
"offhand": "kite", "body": "tabard"}, ...}``. The game keeps that file (it is the source of its hero art) and runs this tool; the engine
only holds the pieces. For each class ``NAME`` it writes

* ``NAME_fight``: twelve 32 by 32 frames (idle, idle, attack, hit, down, cast, item and shoot from the front, then four seen from behind:
  the player's own side is drawn from behind, and the engine refuses a hero personality without ``base_up``) and ``NAME_fight.anim.json``;
* ``NAME_map``: nine frames (down, up and right, each standing and two strides) and ``NAME_map.anim.json``,

so a personality with the job's key can say ``"fight_art": {"sheet": "NAME_fight", "animations": "NAME_fight"}`` and the same for ``map_art``.
The frames are drawn into one RGBA picture and handed to ``terraforma.tools.tilesheet``, which writes the indexed color sheet, the alpha
sheet and the sheet file; the animation files are checked with ``terraforma.animations``. The pictures are made from pixel rules
(flat shapes and one outline), not taken from any other work, and the same looks file gives the same pictures every time.

A look has ``colors`` (any of the roles in ``BASE``, as ``#rrggbb``; ``hood`` and ``mask`` are extra roles for the hood pieces), and these
pieces, each a name from the tables below (the tool names them all if one is wrong): ``head`` (``HEADS``), ``weapon`` (in the hand at rest)
and ``melee`` (in the hand when it strikes; the weapon if left out) from ``CARRY`` (a look with no weapon has fists), ``offhand``
(``OFFHANDS``), ``body`` (``BODIES``), ``ranged`` (``bow``, ``throw`` or ``spark``: what the shoot pose draws), ``bald`` and ``down_cap``
(the color of what is on the head when the hero lies down).

Needs Pillow (``pip install "terraforma[tools]"``); the engine itself does not.
"""

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from terraforma.animations import check_animations
from terraforma.tools import tilesheet

try:
    from PIL import Image
except ImportError:  # the tool alone needs it
    Image = None

SIDE = 32
OUT = "#202028"
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
RANGED = ("bow", "throw", "spark")


class HeroArtError(ValueError):
    """The looks file or an option cannot make hero art."""


FIGHT_POSES = ("idle_a", "idle_b", "attack", "hit", "down", "cast", "item", "shoot")
BACK_POSES = ("back_a", "back_b", "back_attack", "back_hit")
POSES = (*FIGHT_POSES, *BACK_POSES)

BASE = {  # the colors every class shares unless it says otherwise
    "skin": "#e8b890", "hair": "#704020", "cloth": "#3050a0", "cloth_d": "#203070", "trim": "#e0c050", "boot": "#503018",
    "metal": "#c8d0d8", "metal_d": "#808890", "accent": "#c03030", "accent_d": "#802020", "wood": "#a06838", "wood_d": "#6c4420",
    "glow": "#ffd860", "glass": "#80d0e0", "light": "#f4f0e0",
}


@dataclass
class Look:
    """One class: its colors and the pieces it wears and carries (names from the tables of pieces)."""

    colors: dict
    head: str = "hair"
    weapon: str | None = None  # in the hand at rest
    melee: str | None = None  # in the hand when it strikes (the weapon, if left out)
    ranged: str = "throw"  # bow, throw or spark
    offhand: str | None = None
    body: str | None = None
    down_cap: str | None = None  # the color of what is on the head when it lies down
    bald: bool = False

    def __post_init__(self):
        self.colors = {**BASE, **self.colors}
        self.colors.setdefault("hood", self.colors["cloth"])  # (roles a look may leave to follow another)
        self.colors.setdefault("mask", self.colors["accent"])
        self.melee = self.melee or self.weapon


class Canvas:
    """One frame: rows of colors (``#rrggbb``), None where it is clear."""

    def __init__(self):
        self.rows = [[None] * SIDE for _ in range(SIDE)]

    def px(self, x, y, color):
        x, y = int(round(x)), int(round(y))
        if 0 <= x < SIDE and 0 <= y < SIDE:
            self.rows[y][x] = color

    def rect(self, x0, y0, x1, y1, color):
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
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
            self.px(x0 + (x1 - x0) * step / steps, y0 + (y1 - y0) * step / steps, color)

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
                    out.rows[y][x] = OUT
        return out


# --- things worn on the head. (c, L, x, y, view): x is the lean and y the bob; view is side (facing right), front or back -------------------

def head_hair(c, L, x, y, view):
    hair = L["hair"]
    if view == "back":
        c.ellipse(15.5 + x, 9 + y, 5, 5.5, hair)
        return
    c.rect(10 + x, 3 + y, 21 + x, 6 + y, hair)
    c.rect(10 + x, 6 + y, 11 + x, 9 + y, hair)
    if view == "front":
        c.rect(20 + x, 6 + y, 21 + x, 9 + y, hair)


def head_helm(c, L, x, y, view):
    """A knight's helm: steel dome, visor, cheek guard and a plume."""
    c.rect(10 + x, 2 + y, 21 + x, 8 + y, L["metal"])
    c.px(10 + x, 2 + y, None)
    c.px(21 + x, 2 + y, None)
    c.rect(10 + x, 5 + y, 21 + x, 5 + y, L["metal_d"])  # brow rim
    if view == "back":
        c.rect(10 + x, 8 + y, 21 + x, 12 + y, L["metal"])
        c.rect(11 + x, 12 + y, 20 + x, 13 + y, L["metal_d"])  # nape guard
        c.rect(15 + x, 1 + y, 16 + x, 8 + y, L["metal_d"])  # ridge
        c.rect(14 + x, -1 + y, 17 + x, 2 + y, L["accent"])  # plume
        return
    c.rect(10 + x, 8 + y, 12 + x, 12 + y, L["metal_d"])  # cheek guard
    visor = (13, 21) if view == "side" else (12, 19)
    c.rect(visor[0] + x, 7 + y, visor[1] + x, 9 + y, L["metal_d"])
    c.rect(visor[0] + 1 + x, 8 + y, visor[1] - 1 + x, 8 + y, OUT)  # the slit
    if view == "side":
        c.line(14 + x, 1 + y, 8 + x, 4 + y, L["accent"])  # plume streams back
        c.rect(13 + x, 0 + y, 16 + x, 2 + y, L["accent"])
    else:
        c.rect(14 + x, 0 + y, 17 + x, 2 + y, L["accent"])
        c.rect(15 + x, -1 + y, 16 + x, -1 + y, L["accent"])


def head_hood(c, L, x, y, view):
    """A rogue's hood, and a scarf over the mouth."""
    hood, shade, mask = L["hood"], L["cloth_d"], L["mask"]
    if view == "back":
        c.ellipse(15.5 + x, 9 + y, 6.5, 7, hood)
        c.line(15 + x, 3 + y, 15 + x, 14 + y, shade)  # the seam
        c.rect(10 + x, 12 + y, 21 + x, 14 + y, shade)
        return
    c.rect(10 + x, 2 + y, 21 + x, 6 + y, hood)
    c.rect(9 + x, 4 + y, 11 + x, 14 + y, hood)
    c.rect(13 + x, 6 + y, 21 + x, 6 + y, shade)  # the brim shades the eyes
    c.rect(9 + x, 12 + y, 11 + x, 14 + y, shade)
    if view == "front":
        c.rect(20 + x, 4 + y, 22 + x, 14 + y, hood)
        c.rect(12 + x, 11 + y, 19 + x, 13 + y, mask)
    else:
        c.rect(14 + x, 11 + y, 21 + x, 13 + y, mask)


def head_feather_cap(c, L, x, y, view):
    """A ranger's soft cap with a long feather."""
    cap, brim, feather = L["cloth"], L["cloth_d"], L["accent"]
    if view == "back":
        c.ellipse(15.5 + x, 9 + y, 5, 5.5, L["hair"])
        c.rect(10 + x, 2 + y, 21 + x, 6 + y, cap)
        c.rect(9 + x, 6 + y, 22 + x, 6 + y, brim)
        c.line(20 + x, 3 + y, 26 + x, 0 + y, feather)
        return
    head_hair(c, L, x, y, view)
    c.rect(10 + x, 2 + y, 21 + x, 5 + y, cap)
    c.rect(9 + x, 6 + y, 23 + x, 6 + y, brim)
    c.line(12 + x, 3 + y, 6 + x, 0 + y, feather) if view == "side" else c.line(19 + x, 3 + y, 25 + x, 0 + y, feather)
    c.px(11 + x, 3 + y, L["trim"])


def head_headband(c, L, x, y, view):
    """A monk: bare head and a band with its tails."""
    band = L["accent"]
    c.rect(10 + x, 5 + y, 21 + x, 6 + y, band)
    if view == "back":
        c.rect(14 + x, 7 + y, 17 + x, 11 + y, band)
    elif view == "side":
        c.rect(7 + x, 6 + y, 10 + x, 7 + y, band)
        c.rect(6 + x, 8 + y, 9 + x, 9 + y, band)
    else:
        c.px(15 + x, 5 + y, L["trim"])
        c.px(16 + x, 5 + y, L["trim"])


HEADS = {"hair": head_hair, "helm": head_helm, "hood": head_hood, "feather_cap": head_feather_cap, "headband": head_headband}


# --- things carried. (c, L, x, y, how): the hand is at x, y; how is down (at rest) or forward (struck with) --------------------------------

def carry_sword(c, L, x, y, how):
    if how == "down":
        c.rect(x, y - 1, x + 1, y + 5, L["metal"])
        c.rect(x - 1, y - 1, x + 2, y - 1, L["trim"])
    else:
        c.rect(x, y, x + 2, y + 1, L["metal"])
        c.line(x, y - 1, x + 2, y - 7, L["metal"])
        c.line(x + 1, y - 1, x + 3, y - 7, L["metal"])
        c.rect(x - 1, y - 1, x + 3, y - 1, L["trim"])


def carry_dagger(c, L, x, y, how):
    if how == "down":
        c.rect(x - 1, y + 1, x + 2, y + 1, L["metal_d"])  # the guard, under the fist
        c.rect(x, y + 2, x + 1, y + 6, L["metal"])
    else:
        c.line(x, y - 1, x + 1, y - 6, L["metal"])
        c.line(x + 1, y - 1, x + 2, y - 6, L["metal"])
        c.rect(x - 1, y - 1, x + 2, y - 1, L["metal_d"])


def carry_bow(c, L, x, y, how):
    """A bow at rest, held in the near hand (the shoot pose draws its own, drawn)."""
    for dx, dy in ((0, -5), (1, -4), (2, -2), (2, -1), (2, 0), (2, 1), (2, 2), (2, 3), (2, 4), (1, 6), (0, 7)):
        c.px(x + dx, y + dy, L["wood"])
    c.line(x, y - 5, x, y + 7, L["light"])


CARRY = {"sword": carry_sword, "dagger": carry_dagger, "bow": carry_bow}


def shield_kite(c, L, x, y, dy):
    """A kite shield on the left arm, trimmed, with a blazon."""
    top = 14 + y + dy
    c.triangle((3 + x, top), (11 + x, top), (7 + x, top + 11), L["metal"])
    c.rect(3 + x, top, 11 + x, top + 1, L["metal_d"])
    c.rect(7 + x, top + 2, 7 + x, top + 7, L["accent"])
    c.rect(5 + x, top + 4, 9 + x, top + 4, L["accent"])
    c.px(7 + x, top + 8, L["metal_d"])


def offhand_dagger(c, L, x, y, dy):
    """A second blade, held down in the left hand."""
    carry_dagger(c, L, 8 + x, 22 + y + dy, "down")


OFFHANDS = {"kite": shield_kite, "dagger": offhand_dagger}


# --- body pieces. (c, L, x, y, view) -----------------------------------------------------------------------------------------------------

def body_tabard(c, L, x, y, view):
    c.rect(14 + x, 15 + y, 17 + x, 22 + y, L["accent"])
    c.rect(14 + x, 22 + y, 17 + x, 22 + y, L["trim"])
    c.rect(11 + x, 14 + y, 20 + x, 14 + y, L["metal_d"])  # gorget
    c.rect(11 + x, 15 + y, 12 + x, 15 + y, L["metal"])  # pauldron edges
    c.rect(19 + x, 15 + y, 20 + x, 15 + y, L["metal"])


def body_scarf(c, L, x, y, view):
    c.rect(12 + x, 13 + y, 19 + x, 14 + y, L["accent"])
    if view == "front":
        c.rect(18 + x, 15 + y, 19 + x, 20 + y, L["accent"])
    else:
        c.rect(8 + x, 14 + y, 11 + x, 15 + y, L["accent"])
        c.rect(7 + x, 16 + y, 10 + x, 20 + y, L["accent_d"])


def body_quiver(c, L, x, y, view):
    if view == "back":
        c.rect(17 + x, 9 + y, 20 + x, 19 + y, L["wood_d"])  # the quiver over the shoulder
        c.rect(18 + x, 6 + y, 19 + x, 9 + y, L["accent"])  # fletching
        c.line(12 + x, 15 + y, 19 + x, 21 + y, L["trim"])
    elif view == "side":
        c.rect(7 + x, 12 + y, 10 + x, 20 + y, L["wood_d"])
        c.rect(8 + x, 10 + y, 9 + x, 12 + y, L["accent"])
        c.line(12 + x, 14 + y, 19 + x, 21 + y, L["trim"])
    else:
        c.line(12 + x, 14 + y, 19 + x, 21 + y, L["trim"])
        c.rect(19 + x, 9 + y, 20 + x, 13 + y, L["wood_d"])
        c.px(20 + x, 8 + y, L["accent"])


def body_sash(c, L, x, y, view):
    c.rect(11 + x, 19 + y, 20 + x, 21 + y, L["accent"])
    if view == "back":
        c.rect(15 + x, 22 + y, 16 + x, 26 + y, L["accent"])
    else:
        c.rect(17 + x, 22 + y, 18 + x, 25 + y, L["accent"])
        c.px(16 + x, 20 + y, L["trim"])
    c.rect(11 + x, 14 + y, 20 + x, 15 + y, L["cloth_d"])  # the robe's collar


BODIES = {"tabard": body_tabard, "scarf": body_scarf, "quiver": body_quiver, "sash": body_sash}


def head_mitre(c, L, x, y, view):
    """A cleric's tall white mitre with a gold band and cross; long hair below it."""
    head_hair(c, L, x, y, view)
    cap, band = L["cloth"], L["trim"]
    c.triangle((10 + x, 6 + y), (21 + x, 6 + y), (15.5 + x, -1 + y), cap)
    c.rect(10 + x, 5 + y, 21 + x, 6 + y, band)
    if view != "back":
        c.rect(15 + x, 1 + y, 16 + x, 3 + y, band)
        c.rect(14 + x, 2 + y, 17 + x, 2 + y, band)
    else:
        c.rect(14 + x, 7 + y, 17 + x, 12 + y, cap)  # the lappets
        c.rect(14 + x, 12 + y, 17 + x, 12 + y, band)


def head_bard_hat(c, L, x, y, view):
    """A bard's floppy hat with a band and a long curling plume."""
    head_hair(c, L, x, y, view)
    c.rect(11 + x, 1 + y, 20 + x, 5 + y, L["cloth"])
    c.rect(11 + x, 5 + y, 20 + x, 5 + y, L["trim"])
    c.rect(7 + x, 6 + y, 24 + x, 6 + y, L["cloth_d"])
    c.line(19 + x, 2 + y, 24 + x, 0 + y, L["accent"])
    c.px(25 + x, 1 + y, L["accent"])
    c.px(25 + x, 2 + y, L["accent"])


def head_antlers(c, L, x, y, view):
    """A druid's leaf circlet and antlers."""
    head_hair(c, L, x, y, view)
    c.rect(10 + x, 5 + y, 21 + x, 5 + y, L["accent"])
    c.px(12 + x, 4 + y, L["accent"])
    c.px(19 + x, 4 + y, L["accent"])
    for sign, base in ((-1, 12), (1, 19)):
        tip = base + sign * 2
        c.line(base + x, 4 + y, tip + x, 0 + y, L["wood"])
        c.line(base + sign + x, 2 + y, base + sign * 4 + x, 1 + y, L["wood"])
        c.px(tip + sign + x, 0 + y, L["wood"])


def head_cowl(c, L, x, y, view):
    """A warlock's hood with bone horns and glowing eyes."""
    head_hood(c, L, x, y, view)
    c.line(11 + x, 3 + y, 8 + x, 0 + y, L["light"])
    c.line(20 + x, 3 + y, 23 + x, 0 + y, L["light"])
    if view != "back":
        c.px(18 + x, 9 + y, L["accent"])
        c.px(19 + x, 9 + y, L["accent"])
        if view == "front":
            c.px(13 + x, 9 + y, L["accent"])


HEADS.update(mitre=head_mitre, bard_hat=head_bard_hat, antlers=head_antlers, cowl=head_cowl)


def carry_mace(c, L, x, y, how):
    if how == "down":
        c.rect(x, y + 1, x + 1, y + 6, L["wood"])
        c.rect(x - 1, y + 5, x + 2, y + 8, L["metal_d"])
        c.px(x, y + 6, L["metal"])
    else:
        c.line(x, y - 1, x + 2, y - 6, L["wood"])
        c.line(x + 1, y - 1, x + 3, y - 6, L["wood"])
        c.rect(x + 1, y - 10, x + 4, y - 7, L["metal_d"])
        c.px(x + 2, y - 9, L["metal"])


def carry_lute(c, L, x, y, how):
    if how == "down":
        c.line(x + 1, y - 3, x + 1, y + 2, L["wood_d"])  # the neck
        c.ellipse(x + 1, y + 5, 2.6, 3.4, L["wood"])
        c.px(x + 1, y + 5, OUT)
        c.px(x + 1, y - 4, L["trim"])
    else:
        c.line(x, y - 2, x + 3, y - 7, L["wood_d"])
        c.ellipse(x + 1, y - 1, 2.4, 2.4, L["wood"])


def carry_rapier(c, L, x, y, how):
    if how == "down":
        c.rect(x - 1, y + 1, x + 1, y + 1, L["trim"])
        c.rect(x, y + 2, x, y + 9, L["metal"])
    else:
        c.rect(x - 1, y - 1, x + 2, y - 1, L["trim"])
        c.line(x, y - 2, x + 5, y - 9, L["metal"])


def carry_staff(c, L, x, y, how):
    if how == "down":
        c.rect(x, y - 10, x, y + 7, L["wood"])
        c.ellipse(x, y - 11, 2, 2, L["accent"])
    else:
        c.line(x - 1, y + 2, x + 4, y - 10, L["wood"])
        c.line(x, y + 2, x + 5, y - 10, L["wood"])
        c.ellipse(x + 5, y - 11, 2, 2, L["accent"])


def carry_wand(c, L, x, y, how):
    if how == "down":
        c.rect(x, y + 1, x, y + 5, L["wood_d"])
        c.px(x, y + 6, L["glow"])
    else:
        c.line(x, y - 1, x + 3, y - 5, L["wood_d"])
        c.rect(x + 3, y - 7, x + 4, y - 6, L["glow"])


CARRY.update(mace=carry_mace, lute=carry_lute, rapier=carry_rapier, staff=carry_staff, wand=carry_wand)


def shield_symbol(c, L, x, y, dy):
    """A round shield with a gold cross, on the left arm."""
    cy = 19 + y + dy
    c.ellipse(6.5 + x, cy, 4.6, 5, L["metal_d"])
    c.ellipse(6.5 + x, cy, 3.6, 4, L["light"])
    c.rect(6 + x, cy - 3, 7 + x, cy + 3, L["trim"])
    c.rect(4 + x, cy - 1, 9 + x, cy, L["trim"])


def offhand_tome(c, L, x, y, dy):
    """A book held at the left hip, a rune glowing on its cover."""
    top = 20 + y + dy
    c.rect(4 + x, top, 9 + x, top + 6, L["hood"])
    c.rect(9 + x, top, 10 + x, top + 6, L["light"])
    c.rect(4 + x, top, 9 + x, top, L["trim"])
    c.rect(4 + x, top + 6, 9 + x, top + 6, L["trim"])
    c.px(6 + x, top + 3, L["accent"])
    c.px(7 + x, top + 3, L["accent"])
    c.px(6 + x, top + 2, L["accent"])


OFFHANDS.update(symbol=shield_symbol, tome=offhand_tome)


def body_stole(c, L, x, y, view):
    c.rect(13 + x, 14 + y, 14 + x, 22 + y, L["trim"])
    c.rect(17 + x, 14 + y, 18 + x, 22 + y, L["trim"])
    if view != "back":
        c.rect(15 + x, 15 + y, 16 + x, 19 + y, L["trim"])
        c.rect(14 + x, 16 + y, 17 + x, 16 + y, L["trim"])


def body_doublet(c, L, x, y, view):
    c.rect(11 + x, 14 + y, 20 + x, 14 + y, L["trim"])
    if view == "back":
        c.rect(11 + x, 15 + y, 20 + x, 23 + y, L["cloth_d"])
        c.rect(11 + x, 23 + y, 20 + x, 23 + y, L["trim"])
    else:
        for row in (16, 18):
            c.px(15 + x, row + y, L["trim"])
            c.px(16 + x, row + y, L["trim"])


def body_vines(c, L, x, y, view):
    c.line(11 + x, 15 + y, 20 + x, 22 + y, L["accent"])
    for dx, dy in ((13, 16), (16, 19), (18, 21), (12, 19)):
        c.px(dx + x, dy + y, L["accent_d"])
        c.px(dx + 1 + x, dy - 1 + y, L["accent"])


def body_runes(c, L, x, y, view):
    c.rect(11 + x, 14 + y, 20 + x, 14 + y, L["accent_d"])
    for dx, dy in ((13, 17), (16, 16), (18, 18), (15, 20), (17, 21)):
        c.px(dx + x, dy + y, L["accent"])


BODIES.update(stole=body_stole, doublet=body_doublet, vines=body_vines, runes=body_runes)


# --- a figure -----------------------------------------------------------------------------------------------------------------------------

def fight_figure(pose, look):
    """A figure facing right, 32 by 32, or (for a back_ pose) seen from behind."""
    L = look.colors
    c = Canvas()
    back = pose.startswith("back_")
    lean = -2 if pose in ("hit", "back_hit") else 0
    bob = -1 if pose in ("idle_b", "back_b") else 0
    skin, cloth, cloth_d, trim, boot = (L[name] for name in ("skin", "cloth", "cloth_d", "trim", "boot"))
    view = "back" if back else "side"
    if pose == "down":
        c.rect(5, 25, 22, 29, cloth)
        c.rect(5, 28, 22, 29, cloth_d)
        c.rect(22, 26, 27, 29, boot)
        c.ellipse(5, 26.5, 4, 3.5, skin)
        if look.down_cap:
            c.rect(2, 23, 7, 25, look.down_cap)
        elif not look.bald:
            c.rect(2, 23, 5, 25, L["hair"])
        c.px(5, 26, OUT)
        return c.outlined()
    c.rect(12 + lean, 22, 14 + lean, 28, cloth_d)  # legs
    c.rect(17 + lean, 22, 19 + lean, 28, cloth_d)
    c.rect(11 + lean, 28, 14 + lean, 29, boot)
    c.rect(17 + lean, 28, 20 + lean, 29, boot)
    c.rect(11 + lean, 14 + bob, 20 + lean, 22, cloth)  # torso, belt
    c.rect(11 + lean, 20 + bob, 20 + lean, 20 + bob, trim)
    c.ellipse(15.5 + lean, 9 + bob, 5, 5.5, skin if look.bald or not back else L["hair"])
    if look.body:
        BODIES[look.body](c, L, lean, bob, view)
    if not back:
        c.px(18 + lean, 9 + bob, OUT)
        c.px(19 + lean, 9 + bob, OUT)
        if pose == "hit":
            c.px(18 + lean, 8 + bob, OUT)
    if not (look.head == "hair" and look.bald):
        HEADS[look.head](c, L, lean, bob, view)
    arm = lambda x0, y0, x1, y1: c.rect(x0 + lean, y0 + bob, x1 + lean, y1 + bob, cloth)  # noqa: E731
    hand = lambda x_, y_: c.rect(x_ + lean, y_ + bob, x_ + 1 + lean, y_ + 1 + bob, skin)  # noqa: E731
    offhand = OFFHANDS.get(look.offhand)
    if pose in ("idle_a", "idle_b", "hit", "back_a", "back_b", "back_hit"):
        arm(8, 15, 10, 21)
        arm(21, 15, 23, 21)
        hand(8, 22)
        hand(22, 22)
        if offhand:
            offhand(c, L, lean, bob, 0)
        if look.weapon and pose not in ("hit", "back_hit"):
            CARRY[look.weapon](c, L, 23 + lean, 22 + bob, "down")
    elif pose == "attack":
        arm(8, 15, 10, 21)
        arm(21, 15, 28, 17)
        hand(28, 15)
        if offhand:
            offhand(c, L, 0, bob, 0)
        if look.melee:
            CARRY[look.melee](c, L, 29, 15, "forward")
        else:
            c.rect(28, 14, 30, 16, skin)  # a fist, wrapped
            c.rect(28, 14, 28, 16, L["accent"])
    elif pose == "back_attack":
        arm(8, 15, 10, 21)
        arm(21, 7, 23, 15)
        c.rect(22, 5, 23, 6, skin)
        if offhand:
            offhand(c, L, 0, 0, 0)
        if look.melee:
            CARRY[look.melee](c, L, 22, 5, "forward")
        else:
            c.rect(21, 3, 24, 6, skin)
            c.rect(21, 6, 24, 6, L["accent"])
    elif pose == "cast":
        arm(7, 8, 9, 15)
        arm(22, 8, 24, 15)
        hand(7, 6)
        hand(23, 6)
        if offhand:
            offhand(c, L, 0, 0, -5)
        c.px(8, 4, L["glow"])
        c.px(24, 4, L["glow"])
        c.px(8, 3, L["glow"])
        c.px(24, 3, L["glow"])
    elif pose == "item":
        arm(8, 15, 10, 21)
        arm(21, 10, 23, 16)
        hand(22, 8)
        if offhand:
            offhand(c, L, 0, 0, 0)
        c.rect(22, 3, 24, 6, L["glass"])
        c.px(23, 2, L["wood"])
    elif pose == "shoot":
        arm(8, 15, 10, 21)
        arm(21, 13, 27, 15)
        hand(27, 13)
        if offhand:
            offhand(c, L, 0, 0, 0)
        if look.ranged == "bow":
            c.line(29, 8, 29, 20, L["wood"])
            c.line(29, 8, 27, 14, L["light"])
            c.line(29, 20, 27, 14, L["light"])
            c.line(22, 14, 28, 14, L["wood_d"])  # the arrow, nocked
            c.px(28, 14, L["metal"])
            c.px(22, 13, L["accent"])
            c.px(22, 15, L["accent"])
        elif look.ranged == "spark":
            c.rect(29, 12, 30, 15, L["glow"])
            c.px(31, 13, L["glow"])
            c.px(28, 11, L["glow"])
        else:  # something thrown: the blade leaves the hand
            c.line(28, 13, 30, 11, L["metal"])
            c.px(27, 14, L["metal_d"])
    return c.outlined()


def map_figure(facing, step, look):
    """A walking figure: $facing is down, up or right (left is the right one flipped by an alias); $step 0 stands, 1 and 2 stride."""
    L = look.colors
    c = Canvas()
    left_up = 3 if step == 1 else 0
    right_up = 3 if step == 2 else 0
    bob = -1 if step else 0
    side = facing == "right"
    view = "side" if side else "front" if facing == "down" else "back"
    hair_color = L["skin"] if look.bald else L["hair"]
    if side:
        c.rect(14, 23, 17, 28 - left_up, L["cloth_d"])
        c.rect(15, 23, 18, 28 - right_up, L["cloth_d"])
        c.rect(14, 28 - left_up, 18, 29 - left_up, L["boot"])
        c.rect(13, 14 + bob, 19, 23, L["cloth"])
        c.rect(13, 21 + bob, 19, 21 + bob, L["trim"])
        c.ellipse(16, 8.5 + bob, 5, 5.5, L["skin"])
        c.px(19, 9 + bob, OUT)
        if look.body:
            BODIES[look.body](c, L, 1, bob, "side")
        if look.head != "hair":
            HEADS[look.head](c, L, 0, bob, "side")
        elif not look.bald:
            c.rect(11, 3 + bob, 20, 6 + bob, hair_color)
            c.rect(11, 6 + bob, 14, 11 + bob, hair_color)
        arm_swing = 2 if step == 1 else -2 if step == 2 else 0
        c.rect(15 + arm_swing, 15 + bob, 17 + arm_swing, 21 + bob, L["cloth_d"])
        c.rect(15 + arm_swing, 22 + bob, 17 + arm_swing, 23 + bob, L["skin"])
    else:
        c.rect(12, 23, 14, 28 - left_up, L["cloth_d"])
        c.rect(17, 23, 19, 28 - right_up, L["cloth_d"])
        c.rect(11, 28 - left_up, 14, 29 - left_up, L["boot"])
        c.rect(17, 28 - right_up, 20, 29 - right_up, L["boot"])
        c.rect(11, 14 + bob, 20, 23, L["cloth"])
        c.rect(11, 21 + bob, 20, 21 + bob, L["trim"])
        c.rect(8, 15 + bob, 10, 21 + bob + (1 if step == 2 else 0), L["cloth"])
        c.rect(21, 15 + bob, 23, 21 + bob + (1 if step == 1 else 0), L["cloth"])
        c.rect(8, 22 + bob, 9, 23 + bob, L["skin"])
        c.rect(22, 22 + bob, 23, 23 + bob, L["skin"])
        c.ellipse(15.5, 8.5 + bob, 5, 5.5, L["skin"] if facing == "down" or look.bald else hair_color)
        if facing == "down":
            c.px(13, 9 + bob, OUT)
            c.px(18, 9 + bob, OUT)
        if look.body:
            BODIES[look.body](c, L, 0, bob, view)
        if look.head == "hair":
            if not look.bald:
                if facing == "down":
                    c.rect(10, 3 + bob, 21, 6 + bob, hair_color)
                    c.rect(10, 6 + bob, 11, 9 + bob, hair_color)
                    c.rect(20, 6 + bob, 21, 9 + bob, hair_color)
                else:
                    c.ellipse(15.5, 8 + bob, 5.2, 5.6, hair_color)
        else:
            HEADS[look.head](c, L, 0, bob, view)
    return c.outlined()


# --- the classes --------------------------------------------------------------------------------------------------------------------------

# --- sheets and animations ----------------------------------------------------------------------------------------------------------------

def fight_animations():
    at = {pose: number for number, pose in enumerate(POSES)}
    return {
        "animations": {
            "base": {"frames": [at["idle_a"], at["idle_b"]], "ms": 450},
            "hit": {"frames": [at["hit"], at["hit"], at["idle_a"]], "ms": 120, "loop": False},
            "die": {"frames": [at["hit"], {"frame": at["hit"], "rotate": 60}, at["down"]], "ms": 180, "loop": False},
            "attack_close": {"frames": [{"frame": at["attack"], "ms": 260}, at["idle_a"]], "loop": False},
            "run": {"frames": [at["idle_a"], at["idle_b"]], "ms": 90},
            "base_up": {"frames": [at["back_a"], at["back_b"]], "ms": 450},
            "hit_up": {"frames": [at["back_hit"], at["back_hit"], at["back_a"]], "ms": 120, "loop": False},
            "attack_close_up": {"frames": [{"frame": at["back_attack"], "ms": 260}, at["back_a"]], "loop": False},
            "spell": {"frames": [{"frame": at["cast"], "ms": 400}, at["idle_a"]], "loop": False},
            "item": {"frames": [{"frame": at["item"], "ms": 400}, at["idle_a"]], "loop": False},
            "attack_shoot": {"frames": [{"frame": at["shoot"], "ms": 300}, at["idle_a"]], "loop": False},
        },
        "aliases": {
            "flee": {"of": "run", "flip_x": True},
            "die_up": {"of": "die"},
            "skill": {"of": "attack_close", "scale": 1.15},
            "attack_throw": {"of": "attack_shoot"},
            "equip": {"of": "item"},
        },
    }


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


def atlas(frames, columns):
    rows = -(-len(frames) // columns)
    picture = Image.new("RGBA", (columns * SIDE, rows * SIDE), (0, 0, 0, 0))
    for number, frame in enumerate(frames):
        left, top = number % columns * SIDE, number // columns * SIDE
        for y in range(SIDE):
            for x in range(SIDE):
                color = frame.rows[y][x]
                if color:
                    picture.putpixel((left + x, top + y), tuple(int(color[at : at + 2], 16) for at in (1, 3, 5)) + (255,))
    return picture


def write(name, frames, columns, animations, out):
    """The frames as the engine's tilesheet (through its own tool) and the animation file beside it."""
    count = len(frames)
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / f"{name}.png"
        atlas(frames, columns).save(source)
        status = tilesheet.main([str(source), str(out / name), "--frame", f"{SIDE}x{SIDE}", "--columns", str(columns), "--frames", str(count),
                                 "--no-animation"])
    if status:
        raise SystemExit(f"{name}: the tilesheet tool refused the picture")
    sheet = json.loads((out / f"{name}.sheet.json").read_text())
    check_animations(animations, sheet["frames"])
    (out / f"{name}.anim.json").write_text(json.dumps(animations, indent=1) + "\n", encoding="utf-8")


# --- reading the looks file ---------------------------------------------------------------------------------------------------------------

def read_looks(path: Path) -> dict[str, Look]:
    """The looks in $path, checked: every problem is listed at once, naming the class and the field."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HeroArtError(f"{path}: {error}") from error
    if not isinstance(data, dict) or not data:
        raise HeroArtError(f"{path}: expected an object with a look for each class")
    pieces = {"head": HEADS, "weapon": CARRY, "melee": CARRY, "offhand": OFFHANDS, "body": BODIES}
    fields = {"colors", "ranged", "down_cap", "bald", *pieces}
    problems, looks = [], {}
    for key, row in data.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key):
            problems.append(f"{key}: a class key is lowercase letters, digits and _")
        if not isinstance(row, dict):
            problems.append(f"{key}: expected an object")
            continue
        for name in sorted(set(row) - fields):
            problems.append(f"{key}: {name!r} is not a field of a look ({', '.join(sorted(fields))})")
        for name, table in pieces.items():
            if row.get(name) is not None and row[name] not in table:
                problems.append(f"{key}: {name} {row[name]!r} is not one of {', '.join(sorted(table))}")
        colors = row.get("colors", {})
        if not isinstance(colors, dict):
            problems.append(f"{key}: colors is an object of role to #rrggbb")
            colors = {}
        for role, color in colors.items():
            if role not in BASE and role not in ("hood", "mask"):
                problems.append(f"{key}: {role!r} is not a color role ({', '.join((*BASE, 'hood', 'mask'))})")
            if not isinstance(color, str) or not COLOR.fullmatch(color):
                problems.append(f"{key}: color {role} is {color!r}, not #rrggbb")
        if row.get("ranged", "throw") not in RANGED:
            problems.append(f"{key}: ranged {row['ranged']!r} is not one of {', '.join(RANGED)}")
        if row.get("down_cap") is not None and not (isinstance(row["down_cap"], str) and COLOR.fullmatch(row["down_cap"])):
            problems.append(f"{key}: down_cap is {row['down_cap']!r}, not #rrggbb")
        if not problems:
            looks[key] = Look(**{name: value for name, value in row.items()})
    if problems:
        raise HeroArtError(f"{path} has problems:\n  " + "\n  ".join(problems))
    return looks


# --- sheets -------------------------------------------------------------------------------------------------------------------------------

def atlas(frames, columns):
    if Image is None:
        raise HeroArtError('Pillow is not installed: pip install "terraforma[tools]"')
    rows = -(-len(frames) // columns)
    picture = Image.new("RGBA", (columns * SIDE, rows * SIDE), (0, 0, 0, 0))
    for number, frame in enumerate(frames):
        left, top = number % columns * SIDE, number // columns * SIDE
        for y in range(SIDE):
            for x in range(SIDE):
                color = frame.rows[y][x]
                if color:
                    picture.putpixel((left + x, top + y), tuple(int(color[at : at + 2], 16) for at in (1, 3, 5)) + (255,))
    return picture


def write(name, frames, columns, animations, out):
    """The frames as a tilesheet (through ``terraforma.tools.tilesheet``) and the animation file beside it."""
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / f"{name}.png"
        atlas(frames, columns).save(source)
        status = tilesheet.main([str(source), str(out / name), "--frame", f"{SIDE}x{SIDE}", "--columns", str(columns), "--frames", str(len(frames)),
                                 "--no-animation"])
    if status:
        raise HeroArtError(f"{name}: the tilesheet tool refused the picture")
    sheet = json.loads((out / f"{name}.sheet.json").read_text())
    check_animations(animations, sheet["frames"])
    (out / f"{name}.anim.json").write_text(json.dumps(animations, indent=1) + "\n", encoding="utf-8")


def draw(key: str, look: Look, out: Path) -> None:
    """Writes the fight sheet and the map sheet of the class $key, dressed as $look, into the folder $out."""
    Path(out).mkdir(parents=True, exist_ok=True)
    write(f"{key}_fight", [fight_figure(pose, look) for pose in POSES], 4, fight_animations(), Path(out))
    write(f"{key}_map", [map_figure(facing, step, look) for facing in ("down", "up", "right") for step in (0, 1, 2)], 3, map_animations(), Path(out))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("looks", type=Path, help="the looks file (JSON)")
    parser.add_argument("classes", nargs="*", help="which classes to draw (default: all in the file)")
    parser.add_argument("--out", type=Path, required=True, help="the folder to write the sheets into (the game's assets folder)")
    arguments = parser.parse_args(argv)
    try:
        looks = read_looks(arguments.looks)
        unknown = [key for key in arguments.classes if key not in looks]
        if unknown:
            raise HeroArtError(f"{arguments.looks}: no look for {', '.join(unknown)} (it has {', '.join(looks)})")
        for key in arguments.classes or looks:
            draw(key, looks[key], arguments.out)
    except (HeroArtError, tilesheet.TilesheetError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""The animation file: the named animations of one tilesheet (``name.anim.json`` under the game's assets folder).

A public format (the license exception covers it), described in the README under Art. The server never plays an animation; it only
checks the files when it starts, so a typo stops the server, not a player's page. The browser plays them (``client/static/lib/js/animation.js``).
"""

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, ValidationError, model_validator

from .content.schema import Strict

NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AnimationError(ValueError):
    """Says what is wrong with an animation file."""


def _name(value: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError("an animation name is 1-64 lowercase letters, digits or _, starting with a letter")
    return value


Name = Annotated[str, AfterValidator(_name)]


class Transform(Strict):
    """A change to a picture: flipped, then scaled, then rotated (degrees, clockwise), about ``pivot`` ([x, y] in pixels of the frame; the center if left out)."""

    flip_x: bool = False
    flip_y: bool = False
    scale: float = Field(default=1, gt=0, le=16)
    rotate: float = Field(default=0, ge=-360, le=360)
    pivot: list[float] | None = Field(default=None, min_length=2, max_length=2)


class Frame(Transform):
    """One picture of an animation: ``frame`` of the sheet, shown for ``ms`` (the animation's own time if left out), with its own transform."""

    frame: int = Field(ge=0, le=10000)
    ms: int | None = Field(default=None, ge=1, le=60000)


def _frame(value):
    return {"frame": value} if isinstance(value, int) and not isinstance(value, bool) else value


class Animation(Strict):
    """``frames`` (a bare number is a frame with the default time), ``ms`` each is shown for unless it says, whether it ``loop``s (or holds its last frame)
    and ``sync``: ``shared`` (everything playing it is in step) or ``random`` (each starts at a random moment of it)."""

    frames: list[Annotated[Frame, BeforeValidator(_frame)]] = Field(min_length=1, max_length=256)
    ms: int = Field(default=100, ge=1, le=60000)
    loop: bool = True
    sync: Literal["shared", "random"] = "shared"


class Alias(Transform):
    """Another name for the animation or alias ``of``, changed by this transform on top of each frame's own (the frame's first, then the alias's)."""

    of: Name


class AnimationFile(Strict):
    animations: dict[Name, Animation]
    aliases: dict[Name, Alias] = {}

    @model_validator(mode="after")
    def _names_agree(self):
        if not self.animations:
            raise ValueError("a file needs at least one animation")
        for name in self.aliases:
            if name in self.animations:
                raise ValueError(f"{name!r} is both an animation and an alias")
        for name, alias in self.aliases.items():
            seen = {name}
            target = alias.of
            while target in self.aliases:
                if target in seen:
                    raise ValueError(f"alias loop: {name!r} leads back to itself")
                seen.add(target)
                target = self.aliases[target].of
            if target not in self.animations:
                raise ValueError(f"alias {name!r} is of {target!r}, which isn't an animation or an alias")
        return self


def check_animations(data: object, frames: int | None = None) -> AnimationFile:
    """$data (the parsed file) checked; $frames, when the sheet's frame count is known, is the limit for every ``frame``. Raises AnimationError."""
    try:
        checked = AnimationFile.model_validate(data)
    except ValidationError as error:
        raise AnimationError("; ".join(f"{'.'.join(str(part) for part in each['loc'])}: {each['msg']}" for each in error.errors())) from error
    if frames is not None:
        for name, animation in checked.animations.items():
            for each in animation.frames:
                if each.frame >= frames:
                    raise AnimationError(f"animation {name!r} shows frame {each.frame}, but the sheet has {frames} frames")
    return checked


def check_animation_folder(folder: Path) -> None:
    """Every ``*.anim.json`` in $folder checked, against the sheet of the same name when there is one; raises AnimationError naming the file."""
    for path in sorted(Path(folder).glob("*.anim.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AnimationError(f"{path.name}: not JSON ({error})") from error
        frames = None
        sheet = path.with_name(path.name.removesuffix(".anim.json") + ".sheet.json")
        if sheet.is_file():
            try:
                frames = json.loads(sheet.read_text(encoding="utf-8")).get("frames")
            except (OSError, ValueError, AttributeError):
                frames = None
        try:
            check_animations(data, frames if isinstance(frames, int) else None)
        except AnimationError as error:
            raise AnimationError(f"{path.name}: {error}") from error

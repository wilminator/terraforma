"""Where things are: a map and a tile on it.

Every hero, monster, NPC and fight has a location, even before maps are
drawn (the hub is a map too). Map names and coordinates arrive from the
browser, so they are checked like any other input: a name is a short
slug, never a path, and coordinates are whole numbers on the map.
"""

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

MAP_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def check_map_name(name: str) -> str:
    if not MAP_NAME.fullmatch(name):
        raise ValueError("a map name is 1-64 lowercase letters, digits, _ or -, starting with a letter or digit")
    return name


MapName = Annotated[str, AfterValidator(check_map_name)]


class Position(BaseModel):
    """A tile, as the browser sends it. Checked against the map's size by Map.contains()."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    map: MapName
    x: int
    y: int


class Located:
    """Mixin: the model sits on a map, at a tile."""

    @declared_attr
    def map_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("maps.id"), index=True)

    x: Mapped[int] = mapped_column(Integer, default=0)
    y: Mapped[int] = mapped_column(Integer, default=0)

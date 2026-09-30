"""The map-ready base: seeded randomness and checked locations."""

import pytest
from pydantic import ValidationError

from terraforma.models import Map
from terraforma.world.location import Position
from terraforma.world.rng import WorldRng


def test_the_same_seed_and_stream_give_the_same_numbers():
    a = [WorldRng(42).stream("fight", 7, 1).randint(1, 1000) for _ in range(1)]
    b = [WorldRng(42).stream("fight", 7, 1).randint(1, 1000) for _ in range(1)]
    assert a == b
    rolls = WorldRng(42).stream("loot", 3)
    again = WorldRng(42).stream("loot", 3)
    assert [rolls.random() for _ in range(20)] == [again.random() for _ in range(20)]


def test_streams_are_independent_of_each_other_and_of_the_seed():
    rng = WorldRng(42)
    first = [rng.stream("fight", 7, 1).random() for _ in range(3)]
    assert len(set(first)) == 1, "asking for a stream again starts it over"
    assert rng.stream("fight", 7, 1).random() != rng.stream("fight", 7, 2).random()
    assert WorldRng(42).stream("map").random() != WorldRng(43).stream("map").random()


def test_names_are_not_ambiguous():
    rng = WorldRng(1)
    assert rng.stream("a", "bc").random() != rng.stream("ab", "c").random()


@pytest.mark.parametrize("name", ["hub", "castle-2", "cave_of_echoes", "0", "a" * 64])
def test_good_map_names(name):
    assert Position(map=name, x=0, y=0).map == name


@pytest.mark.parametrize("name", ["", "Hub", "../etc/passwd", "maps/hub", "hub.map", "-hub", "a" * 65, "hub\x00"])
def test_map_names_that_could_be_paths_or_worse_are_refused(name):
    with pytest.raises(ValidationError):
        Position(map=name, x=0, y=0)


@pytest.mark.parametrize("bad", [{"x": "1", "y": 0}, {"x": 1.5, "y": 0}, {"x": 0, "y": None}, {"x": 0, "y": 0, "z": 1}])
def test_coordinates_are_whole_numbers_and_nothing_else(bad):
    with pytest.raises(ValidationError):
        Position(map="hub", **bad)


def test_a_map_knows_its_own_tiles():
    hub = Map(name="hub", width=10, height=5)
    assert hub.contains(Position(map="hub", x=0, y=0))
    assert hub.contains(Position(map="hub", x=9, y=4))
    assert not hub.contains(Position(map="hub", x=10, y=0))
    assert not hub.contains(Position(map="hub", x=-1, y=0))
    assert not hub.contains(Position(map="town", x=0, y=0)), "a tile on another map"

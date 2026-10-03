"""Fights between parties: PvP is off by default, a game opens places, and the range window picks who may be fought."""

import pytest

from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.pvp import service
from terraforma.pvp.hooks import PvpZones

pytestmark = pytest.mark.anyio


class Strength(Rules):
    """Rules where a fighter is just its strength."""

    def pxp(self, fighter) -> int:
        return fighter


class Wilds(PvpZones):
    """Everywhere but map 1 (a town) permits PvP."""

    async def allows_pvp(self, session, map_id, x, y):
        return map_id != 1


def test_refused_where_pvp_is_off():
    assert Rules().may_start_pvp(100, 500, False) is not None


def test_a_stronger_or_equal_party_is_always_allowed():
    rules = Rules()
    assert rules.may_start_pvp(100, 100, True) is None
    assert rules.may_start_pvp(100, 10_000, True) is None


def test_the_window_is_85_percent_by_default():
    rules = Rules()
    assert rules.may_start_pvp(100, 85, True) is None
    assert rules.may_start_pvp(100, 84, True) is not None


def test_a_game_can_change_the_window():
    class Wide(Rules):
        pvp_window = 0.5

    assert Wide().may_start_pvp(100, 50, True) is None
    assert Wide().may_start_pvp(100, 49, True) is not None


def test_party_pxp_sums_its_fighters():
    assert service.party_pxp(Strength(), [10, 20, 30]) == 60


async def test_default_zones_allow_nowhere():
    assert await PvpZones().allows_pvp(None, 1, 0, 0) is False
    assert isinstance(Game(name="Test").pvp, PvpZones)


async def test_refusal_uses_the_place_and_the_summed_parties():
    rules = Strength()
    assert await service.refusal(None, Wilds(), rules, [50, 50], [90], 2, 0, 0) is None
    assert await service.refusal(None, Wilds(), rules, [50, 50], [84], 2, 0, 0) is not None
    assert await service.refusal(None, Wilds(), rules, [50, 50], [900], 1, 0, 0) is not None

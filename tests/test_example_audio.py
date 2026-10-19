"""The example game's audio: five tunes and fourteen sound effects written in MML, each with its MP3 render beside it.

The sources are chipsynth tunes (``assets/audio/music`` and ``assets/audio/sfx``). Every source has its render and every
render its source, every file is recorded in ``assets/ASSETS.md``, every render is an MP3 with sound in it, and, when
chipsynth is installed (``pip install git+https://github.com/wilminator/chipsynth``), every source parses with no warnings
and is the length its place asks for: a tune of 8 to 16 bars, an effect a fraction of a second long that plays once.
"""

import re

import pytest

from terraforma.example import GAME

pytestmark = [pytest.mark.platform]

AUDIO = GAME.assets_dir / "audio"
TUNES = {"title": False, "game_over": True, "map": False, "combat": False, "boss": False}     # name -> plays once
EFFECTS = ("menu_move", "menu_confirm", "menu_cancel", "panel_open", "panel_close", "fight_start", "attack", "hit", "miss",
           "heal", "died", "victory", "defeat", "level_up")
#: The effects allowed to run longer than half a second: a death, the stings after a fight and the level-up climb.
LONGER = {"died", "victory", "defeat", "level_up"}


def sources(folder):
    return {path.stem: path for path in (AUDIO / folder).glob("*.mml")}


def test_the_set_is_five_tunes_and_fourteen_effects_each_with_its_render():
    assert set(sources("music")) == set(TUNES)
    assert set(sources("sfx")) == set(EFFECTS)
    for folder in ("music", "sfx"):
        stems = {path.stem for path in (AUDIO / folder).glob("*.mp3")}
        assert stems == set(sources(folder)), f"{folder}: every .mml has its .mp3 beside it and vice versa"
        others = {path.name for path in (AUDIO / folder).iterdir() if path.suffix not in (".mml", ".mp3")}
        assert not others, f"{folder}: only sources and renders live here, not {sorted(others)}"


def test_every_audio_file_is_recorded_in_assets_md():
    record = (GAME.assets_dir / "ASSETS.md").read_text()
    recorded = set(re.findall(r"`([^`]+)`", record))
    for path in sorted(AUDIO.rglob("*")):
        if path.is_file():
            assert path.relative_to(GAME.assets_dir).as_posix() in recorded, f"{path.name} is not in ASSETS.md"


def test_every_render_is_an_mp3_with_sound_in_it():
    for path in sorted(AUDIO.rglob("*.mp3")):
        data = path.read_bytes()
        assert len(data) > 1024, f"{path.name} is too small to hold a sound"
        frame_sync = data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        assert data[:3] == b"ID3" or frame_sync, f"{path.name} does not start like an MP3"


def test_every_source_parses_with_chipsynth_and_is_the_length_its_place_asks_for():
    mml = pytest.importorskip("chipsynth.mml", reason="chipsynth is not installed (pip install git+https://github.com/wilminator/chipsynth)")
    for name, once in TUNES.items():
        tune = mml.parse(sources("music")[name].read_text())
        assert not tune.warnings, f"{name}: {tune.warnings}"
        assert 8 <= tune.bars <= 16, f"{name} is {tune.bars} bars"
        assert tune.once == once, f"{name} {'plays once' if once else 'loops'}"
        assert tune.loop_tick == 0, f"{name} loops from the top (the MP3 is cut at the loop end, so a player loops the file)"
        assert all(tune.voices[index] for index in range(3)), f"{name} uses a lead, a second voice and a bass"
    for name in EFFECTS:
        tune = mml.parse(sources("sfx")[name].read_text())
        assert not tune.warnings, f"{name}: {tune.warnings}"
        assert tune.once, f"{name} is a one-shot"
        assert tune.seconds <= (2.0 if name in LONGER else 0.5), f"{name} runs {tune.seconds:.2f} s before its release"

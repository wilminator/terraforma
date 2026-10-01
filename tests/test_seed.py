"""Seed data: a game's starting content, as JSON files the engine reads and checks."""

import json

import pytest

from terraforma.seed import SeedError, load_seed


def test_the_loader_reads_each_file_by_name(tmp_path):
    (tmp_path / "monsters.json").write_text(json.dumps([{"name": "Slime", "stats": {"HP": 5}}]))
    (tmp_path / "items.json").write_text("[]")
    (tmp_path / "notes.txt").write_text("not seed")
    assert load_seed(tmp_path) == {"items": [], "monsters": [{"name": "Slime", "stats": {"HP": 5}}]}


def test_the_loader_refuses_malformed_seed(tmp_path):
    (tmp_path / "items.json").write_text(json.dumps({"not": "a list"}))
    with pytest.raises(SeedError):
        load_seed(tmp_path)
    (tmp_path / "items.json").write_text("[{]")
    with pytest.raises(SeedError):
        load_seed(tmp_path)
    (tmp_path / "items.json").write_text("[1, 2]")
    with pytest.raises(SeedError):
        load_seed(tmp_path)

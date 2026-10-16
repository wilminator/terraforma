"""Seed data: a game's starting content, as JSON files the engine reads and checks."""

import json

import pytest

from terraforma.seed import SeedError, load_seed

pytestmark = pytest.mark.platform


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


def write(folder, name, rows):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))


def test_a_kind_may_be_a_folder_of_files_beside_or_instead_of_its_one_file(tmp_path):
    write(tmp_path, "monsters.json", [{"key": "rat"}])
    write(tmp_path, "monsters/b.json", [{"key": "bat"}])
    write(tmp_path, "monsters/a.json", [{"key": "ant"}])
    write(tmp_path, "monsters/deep/c.json", [{"key": "cat"}])
    write(tmp_path, "items/only.json", [{"key": "herb"}])
    (tmp_path / "monsters" / "notes.txt").write_text("not seed")
    write(tmp_path, "monsters/.hidden.json", [{"key": "ghost"}])
    (tmp_path / "empty").mkdir()
    assert load_seed(tmp_path) == {"items": [{"key": "herb"}], "monsters": [{"key": "rat"}, {"key": "ant"}, {"key": "bat"}, {"key": "cat"}]}


def test_a_key_in_two_files_of_a_kind_is_refused_naming_both(tmp_path):
    write(tmp_path, "monsters.json", [{"key": "rat"}])
    write(tmp_path, "monsters/more.json", [{"key": "rat"}])
    with pytest.raises(SeedError, match=r"monsters: the key 'rat' is in monsters.json and in monsters/more.json"):
        load_seed(tmp_path)
    (tmp_path / "monsters.json").unlink()
    write(tmp_path, "monsters/a/x.json", [{"key": "rat"}])
    with pytest.raises(SeedError, match=r"in monsters/a/x.json and in monsters/more.json"):
        load_seed(tmp_path)


def test_the_same_key_in_different_kinds_is_fine_and_a_bad_file_in_a_folder_is_named(tmp_path):
    write(tmp_path, "monsters/a.json", [{"key": "x"}])
    write(tmp_path, "items/a.json", [{"key": "x"}])
    assert load_seed(tmp_path).keys() == {"monsters", "items"}
    (tmp_path / "monsters" / "bad.json").write_text("[{]")
    with pytest.raises(SeedError, match=r"monsters/bad.json: not valid JSON"):
        load_seed(tmp_path)

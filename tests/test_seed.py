"""Seed data: DragonStar's cache files converted to JSON, and the loader."""

import importlib.util
import json
from pathlib import Path

import pytest

from terraforma.seed import SeedError, load_seed

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("convert", ROOT / "utils" / "convert_dragonstar_cache.py")
convert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(convert)

# The shape of a DragonStar cache file: var_export() of the table's rows.
CACHE = r"""<?php
// GENERATED -- do not hand-edit.
return array (
  0 =>
  array (
    'abilityid' => 1,
    'name' => 'Ogre\'s Club \\ Fist',
    'type' => 0,
    'effect_spec' => '{"effect":2,"targets":-1,"base":5,"added":2,"attribute":0}',
    'mp_used' => 0,
    'chance' => 0.25,
    'owner' => NULL,
    'active' => true,
    'hidden' => false,
    'debt' => -3,
    'note' => '[not json',
    'lines' => 'one
two',
  ),
  1 =>
  array (
    'abilityid' => 2,
    'name' => 'Empty',
    'tags' => '[]',
    'nested' =>
    array (
      'a' => 1,
      5 => 'five',
    ),
  ),
);"""


def test_var_export_is_read_value_for_value():
    rows = convert.parse_var_export(CACHE)
    first = rows[0]
    assert first["name"] == "Ogre's Club \\ Fist", "escaped quote and backslash"
    assert (first["chance"], first["owner"], first["active"], first["hidden"], first["debt"]) == (0.25, None, True, False, -3)
    assert first["lines"] == "one\ntwo"
    assert rows[1]["nested"] == {"a": 1, "5": "five"}, "a keyed array becomes an object"
    assert isinstance(rows, list), "a 0..n array becomes a list"


def test_json_text_columns_become_json():
    row = convert.decode_json_columns(convert.parse_var_export(CACHE)[0])
    assert row["effect_spec"] == {"effect": 2, "targets": -1, "base": 5, "added": 2, "attribute": 0}
    assert row["note"] == "[not json", "text that only looks like JSON stays text"
    assert convert.decode_json_columns({"tags": "[]"}) == {"tags": []}


def test_the_file_is_read_never_run():
    with pytest.raises(convert.ParseError):
        convert.parse_var_export("<?php system('rm -rf /'); return array ();")


def test_converting_a_cache_folder_writes_seed_the_loader_reads(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "abilities.php").write_text(CACHE)
    (cache / "monsters.php").write_text("<?php return array (\n);")
    out = tmp_path / "seed"
    assert convert.convert(cache, out) == {"abilities": 2, "monsters": 0}
    seed = load_seed(out)
    assert seed["abilities"][0]["effect_spec"]["effect"] == 2
    assert seed["monsters"] == []


def test_the_loader_refuses_malformed_seed(tmp_path):
    (tmp_path / "items.json").write_text(json.dumps({"not": "a list"}))
    with pytest.raises(SeedError):
        load_seed(tmp_path)
    (tmp_path / "items.json").write_text("[{]")
    with pytest.raises(SeedError):
        load_seed(tmp_path)

"""Exporting the planned state to a file and importing one into the draft: the file's checks, the module match, the frozen draft, the
admin's calls and the command line. On every database."""

import asyncio
import json

import pytest

from terraforma.__main__ import main, run_state_command
from terraforma.game import Game
from terraforma.gamestate import service, transfer
from terraforma.gamestate.document import hash_of

from .helpers import expect
from .test_game_state import SECTIONS, Bonus, Rates, an_admin
from .test_towns import sign_in

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


def file_with(xp=5, modules=(), source="planned"):
    document = {"format": 1, "sections": {"rates": {"xp": xp}, "bonus": {}}}
    return {"export_format": 1, "kind": "planned-state", "source": source, "hash": hash_of(document), "modules": list(modules), "document": document}


# --- exporting -----------------------------------------------------------------------------------------------------------

async def test_an_export_holds_the_document_its_hash_and_the_installed_modules(db):
    await service.edit_section(db, SECTIONS, "rates", {"xp": 3}, None)
    planned = await transfer.export(db, SECTIONS, ["pack"])
    live = await transfer.export(db, SECTIONS, ["pack"], "live")
    assert (planned["source"], planned["modules"], planned["document"]["sections"]["rates"]) == ("planned", ["pack"], {"xp": 3})
    assert planned["hash"] == hash_of(planned["document"]) != live["hash"]
    assert (live["source"], live["document"]["sections"]["rates"]) == ("live", {"xp": 1.0})
    with pytest.raises(service.StateError):
        await transfer.export(db, SECTIONS, [], "elsewhere")


async def test_what_one_server_exports_another_imports_to_the_same_hash(db):
    await service.edit_section(db, SECTIONS, "rates", {"xp": 3, "gold": 0.1 + 0.2}, None)
    data = json.loads(json.dumps(await transfer.export(db, SECTIONS, ["pack"])))  # (through a file's text)
    await service.revert(db, SECTIONS, None)
    imported = await transfer.import_file(db, SECTIONS, ["pack"], data, None)
    assert imported.planned_hash == data["hash"] and imported.planned.frozen and imported.differs


# --- importing -----------------------------------------------------------------------------------------------------------

async def test_an_import_fills_the_frozen_draft_and_leaves_live_alone(db):
    before = await service.state(db, SECTIONS)
    after = await transfer.import_file(db, SECTIONS, [], file_with(7), None)
    assert (after.planned.document["sections"]["rates"], after.planned.frozen) == ({"xp": 7}, True)
    assert after.live.id == before.live.id and after.live.document == before.live.document
    assert (await service.edit_section(db, SECTIONS, "bonus", {}, None)).planned.frozen is False, "any edit unfreezes it"


async def test_an_import_can_be_committed_like_any_draft(db):
    imported = await transfer.import_file(db, SECTIONS, [], file_with(7), None)
    done = await service.commit(db, SECTIONS, None, imported.planned_hash, "from test")
    assert done.live.document["sections"]["rates"] == {"xp": 7} and not done.planned.frozen


async def test_a_file_made_with_other_add_ons_is_refused_and_nothing_changes(db):
    before = await service.state(db, SECTIONS)
    for installed, made_with in (([], ["pack"]), (["pack"], []), (["a", "b"], ["b", "a"])):
        with pytest.raises(service.Refused) as refused:
            await transfer.import_file(db, SECTIONS, installed, file_with(7, made_with), None)
        assert "add-ons" in refused.value.problems[0].message
    assert await transfer.import_file(db, SECTIONS, ["a", "b"], file_with(7, ["a", "b"]), None)
    assert before.planned.revision == 0


async def test_a_file_that_is_not_a_good_export_is_refused_with_every_problem(db):
    good = file_with()
    cases = [
        ([], "holds exactly"),
        ({**good, "extra": 1}, "holds exactly"),
        ({key: value for key, value in good.items() if key != "hash"}, "holds exactly"),
        ({**good, "export_format": 2}, "not a format 1"),
        ({**good, "kind": "something"}, "not a format 1"),
        ({**good, "source": "elsewhere"}, "source must be"),
        ({**good, "modules": "pack"}, "list of add-on keys"),
        ({**good, "hash": "0" * 64}, "does not match the file's hash"),
        ({**good, "document": {"format": 1, "sections": {"rates": {"xp": 9}, "bonus": {}}}}, "does not match the file's hash"),
        ({**good, "document": {"format": 1, "sections": {"x": float("nan")}}}, "not plain JSON"),
    ]
    for data, why in cases:
        with pytest.raises(service.Refused) as refused:
            await transfer.import_file(db, SECTIONS, [], data, None)
        assert any(why in problem.message for problem in refused.value.problems), (data, why)
    assert (await service.state(db, SECTIONS)).planned.revision == 0


async def test_a_document_that_does_not_check_out_on_this_server_is_refused(db):
    bad = {"format": 1, "sections": {"rates": {"xp": -1}, "bonus": {}, "ghost": {}}}
    data = {**file_with(), "document": bad, "hash": hash_of(bad)}
    with pytest.raises(service.Refused) as refused:
        await transfer.import_file(db, SECTIONS, [], data, None)
    assert {problem.section for problem in refused.value.problems} == {"rates", "ghost"}
    assert (await service.state(db, SECTIONS)).planned.frozen is False


# --- the admin's calls ---------------------------------------------------------------------------------------------------

@pytest.fixture
def game():
    return Game(name="Test Game", state_sections=(Rates(), Bonus()))


def test_an_admin_exports_a_file_and_imports_it_and_the_draft_comes_back_frozen(app_client):
    root = an_admin(app_client)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {"xp": 4}}, headers=root), 200)
    answer = expect(app_client.get("/api/admin/state/export", headers=root), 200)
    assert "attachment" in answer.headers["content-disposition"] and ".json" in answer.headers["content-disposition"]
    data = answer.json()
    assert (data["source"], data["modules"], data["document"]["sections"]["rates"]) == ("planned", [], {"xp": 4})
    live = expect(app_client.get("/api/admin/state/export?source=live", headers=root), 200).json()
    assert live["document"]["sections"]["rates"] == {"xp": 1.0}
    expect(app_client.post("/api/admin/state/revert", headers=root), 200)
    imported = expect(app_client.post("/api/admin/state/import", json=data, headers=root), 200).json()
    assert (imported["planned"]["frozen"], imported["planned"]["hash"], imported["planned"]["differs"]) == (True, data["hash"], True)
    assert expect(app_client.get("/api/admin/state/live", headers=root), 200).json()["document"]["sections"]["rates"] == {"xp": 1.0}
    committed = expect(app_client.post("/api/admin/state/commit", json={"hash": data["hash"]}, headers=root), 200).json()
    assert committed["live"]["hash"] == data["hash"] and committed["planned"]["frozen"] is False


def test_an_import_with_other_add_ons_or_a_bad_hash_answers_with_the_problems(app_client):
    root = an_admin(app_client)
    refused = expect(app_client.post("/api/admin/state/import", json=file_with(modules=["pack"]), headers=root), 422).json()["detail"]
    assert "nothing was changed" in refused["message"] and "add-ons" in refused["problems"][0]["message"]
    expect(app_client.post("/api/admin/state/import", json={**file_with(), "hash": "f" * 64}, headers=root), 422)
    assert expect(app_client.get("/api/admin/state", headers=root), 200).json()["planned"]["frozen"] is False


def test_the_calls_take_only_what_they_name_and_only_an_admin_may_use_them(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/state/import", json={**file_with(), "surprise": 1}, headers=root), 422)
    expect(app_client.post("/api/admin/state/import", json={**file_with(), "source": "elsewhere"}, headers=root), 422)
    expect(app_client.post("/api/admin/state/import", json={key: value for key, value in file_with().items() if key != "document"}, headers=root), 422)
    expect(app_client.get("/api/admin/state/export?source=elsewhere", headers=root), 422)
    assert app_client.post("/api/admin/state/import", json=file_with(), headers={}).status_code in (401, 403), "no CSRF token"
    mike = sign_in(app_client, "Mike")
    expect(app_client.get("/api/admin/state/export", headers=mike), 403)
    expect(app_client.post("/api/admin/state/import", json=file_with(), headers=mike), 403)


# --- the command line ----------------------------------------------------------------------------------------------------

def run_command(client, action, path, live=False):
    return asyncio.run(run_state_command(client.app, action, path, live))


def test_the_command_line_exports_and_imports_a_file(app_client, tmp_path, capsys):
    root = an_admin(app_client)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {"xp": 4}}, headers=root), 200)
    path = tmp_path / "state.json"
    assert run_command(app_client, "export-state", path) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data["source"], data["document"]["sections"]["rates"]) == ("planned", {"xp": 4})
    assert run_command(app_client, "export-state", tmp_path / "live.json", live=True) == 0
    assert json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))["source"] == "live"
    expect(app_client.post("/api/admin/state/revert", headers=root), 200)
    assert run_command(app_client, "import-state", path) == 0
    assert "frozen" in capsys.readouterr().out
    assert expect(app_client.get("/api/admin/state", headers=root), 200).json()["planned"]["frozen"] is True


def test_the_command_line_says_what_is_wrong_with_a_file(app_client, tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**file_with(), "hash": "0" * 64}), encoding="utf-8")
    assert run_command(app_client, "import-state", path) == 1
    assert "does not match the file's hash" in capsys.readouterr().out
    path.write_text("not json", encoding="utf-8")
    assert run_command(app_client, "import-state", path) == 1
    assert run_command(app_client, "import-state", tmp_path / "missing.json") == 1
    assert "Can't read" in capsys.readouterr().out


def test_the_command_line_checks_its_arguments(capsys):
    assert main(["terraforma", "export-state"]) == 2
    assert main(["terraforma", "import-state", "file.json", "--live"]) == 2
    assert main(["terraforma", "export-state", "file.json", "--other"]) == 2

"""The Drops panel in a real browser: loot a fight held for the party. A need/want drop waits for every hero that fought to answer and then the
server rolls; a hand-out is given by one hero to another (the calls behind it are tests/test_pending_drops.py). The fight and its held drops are
made in the database: a team of two (Aria and Bram) in a fight with a slime, and a drop of a potion for the team's side."""

from playwright.sync_api import expect
from sqlalchemy import select

from terraforma.fights import live
from terraforma.fights.models import PendingDrop
from terraforma.heroes.models import Team, TeamMember

from .test_heroes import tab
from .test_pack import Player, start


def hold(live_server, hero_id, *modes):
    """A fight the hero's team has won, with a potion held in each of $modes (need_want or assign) for the team's side."""
    rules = live_server.game.rules

    async def work(db):
        team = await db.get(Team, await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id)))
        record = await live.start_team_fight(db, team, ["slime"], rules)
        record.finished = True  # (drops are held when a fight ends; a running one would also take the page to the fight)
        for number, mode in enumerate(modes):
            db.add(PendingDrop(fight_id=record.id, number=number, party=0, item_key="potion", qty=1, mode=mode, map_id=record.map_id, x=record.x, y=record.y))

    live_server.in_db(work)


def reload_to_drops(page):
    """The page reads the world again (the drop was made behind its back) and, once it has started, opens the Drops panel."""
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Drops")


def test_nothing_is_waiting_when_no_fight_held_anything(page, live_server):
    start(page, live_server, {"Alpha": ["Aria"]})
    tab(page, "Drops")
    expect(page.locator("#drops-none")).to_have_text("No loot is waiting for this hero.")


def test_the_party_answers_and_the_server_says_who_won(page, live_server):
    player = start(page, live_server, {"Alpha": ["Aria", "Bram"]})
    heroes = player.heroes()
    hold(live_server, heroes["Aria"], "need_want")
    reload_to_drops(page)
    row = page.locator("[data-drop]")
    expect(row).to_contain_text("1 × Potion")
    expect(row).to_contain_text("(you have not answered)")
    page.get_by_role("button", name="Need Potion").click()
    expect(row).to_contain_text("(you said Need)")
    expect(page.locator("[data-choice='need']")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#drops-settled")).to_have_count(0)  # (Bram has not answered)

    page.locator("#drops-hero").select_option(label="Bram")
    expect(row).to_contain_text("(you have not answered)")  # (their answers stay hidden: this is Bram's own)
    page.get_by_role("button", name="Pass Potion").click()
    expect(page.locator("[data-settled]")).to_contain_text("Aria got 1 × Potion.")
    expect(page.locator("[data-settled]")).to_contain_text("Aria (Need) rolled")
    expect(page.locator("#drops-none")).to_be_visible()


def test_a_drop_is_handed_to_a_hero_of_the_party(page, live_server):
    player = start(page, live_server, {"Alpha": ["Aria", "Bram"]})
    hold(live_server, player.heroes()["Aria"], "assign")
    reload_to_drops(page)
    expect(page.locator("[data-drop]")).to_contain_text("(handed out by the party)")
    page.get_by_label("Give Potion to").select_option(label="Bram")
    page.get_by_role("button", name="Hand out Potion").click()
    expect(page.locator("[data-settled]")).to_have_text("Bram got 1 × Potion.")
    expect(page.locator("#drops-none")).to_be_visible()


def test_the_fight_scene_tells_the_party_a_drop_is_held(page, live_server):
    start(page, live_server, {"Alpha": ["Aria"]})
    said = page.evaluate(
        """async () => {
          const { FightModel } = await import("/client/js/fight-model.js");
          const model = new FightModel({ id: 1, round: 1, over: false, vital: "HP", fighters: [] });
          return model.apply(["DropHeld", [0, "potion", 2, "need_want"]]).text;
        }"""
    )
    assert said == ["fight.drop_held", {"item": "potion", "quantity": 2}]

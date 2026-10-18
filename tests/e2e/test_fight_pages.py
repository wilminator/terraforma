"""The fight pages in a real browser (/fights/): the public list and a fight played back with the fight scene, the lists of a signed-in player, and a
team's history (the calls behind them are tests/test_fight_pages.py). The fight is played to its end in the database first: Mike's Aria, strong, against
one frail slime, so the dice only decide how many rounds it takes."""

from playwright.sync_api import expect
from sqlalchemy import select

from terraforma.fightpages.models import PublicFight
from terraforma.fights import live
from terraforma.fights.combatant import Command
from terraforma.heroes.models import Hero, Team, TeamMember

from ..test_monster_rewards import set_hp
from .test_heroes import open_game
from .test_pack import Player

SLOW = 20000


def a_finished_fight(live_server, hero_id, public=True):
    """Aria's team fights a slime until one side is down; returns the fight's number and public name."""
    rules = live_server.game.rules

    async def work(db):
        team = await db.get(Team, await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id)))
        owner = (await db.get(Hero, hero_id)).account_id
        record = await live.start_team_fight(db, team, ["slime"], rules)
        await set_hp(db, record.id, 1, 1)
        await set_hp(db, record.id, 0, 500, strength=60)
        for _ in range(30):
            if record.finished:
                break
            await live.submit_command(db, record, owner, (0, 0, 0), Command.ATTACK_RIGHT, 0, (1, 0, 0), rules)
            await live.resolve_round(db, record, rules)
        assert record.finished
        if public:
            db.add(PublicFight(fight_id=record.id))
        return record.id, record.guid

    return live_server.in_db(work)


def faster(page):
    """The fastest speed: the playback is paced for reading, and a test need not wait for that."""
    for _ in range(2):
        page.locator("#fight-speed").click()


def start(page, live_server, public=True):
    """Mike, with a team of Aria and one finished fight; the page is not on the fights yet."""
    open_game(page, live_server)
    mike = Player(page, "Mike")
    mike.post("/api/teams", {"name": "Alpha", "heroes": [{"name": "Aria", "job": "fighter"}]})
    return a_finished_fight(live_server, mike.heroes()["Aria"], public)


def test_the_public_list_opens_a_fight_that_plays_back_to_its_end(page, live_server):
    fight_id, _guid = start(page, live_server)
    page.goto("/fights/")
    expect(page.locator(f"#fights-public li[data-fight='{fight_id}']")).to_be_visible()
    page.locator(f"#fights-public li[data-fight='{fight_id}'] a").click()
    stage = page.locator("#stage")
    expect(stage).to_have_attribute("data-scene", "fight")
    expect(stage).to_have_attribute("data-link", "open", timeout=SLOW)
    faster(page)
    expect(stage).to_have_attribute("data-over", "true", timeout=SLOW)
    expect(page.locator(".fight-log")).to_contain_text("Aria")
    expect(page.locator("[data-menu='commands']")).to_have_count(0)  # (nobody commands a fight played back)
    page.locator("#fight-leave").click()
    expect(page.locator("#fights-public-card")).to_be_visible()


def test_the_replay_starts_with_everyone_standing_and_ends_as_the_fight_did(page, live_server):
    fight_id, guid = start(page, live_server)
    page.goto(f"/fights/?watch={guid}")
    stage = page.locator("#stage")
    expect(stage).to_have_attribute("data-link", "open", timeout=SLOW)
    faster(page)
    expect(page.locator(".fighter")).to_have_count(2)  # the hero and the slime, both there before the first blow
    expect(stage).to_have_attribute("data-over", "true", timeout=SLOW)
    ended = page.request.get(f"/api/fight-pages/{fight_id}/replay").json()["fight"]["fighters"]
    slime = next(each for each in ended if each["party"] == 1)
    assert slime["resources"]["HP"][0] == 0
    expect(page.locator("[data-fighter='1.0.0']")).to_have_count(1)


def test_a_signed_in_player_sees_their_fight_and_their_teams_history(page, live_server):
    fight_id, _guid = start(page, live_server, public=False)
    page.goto("/fights/")
    expect(page.locator("#fights-public-none")).to_be_visible()  # (nothing is public)
    row = page.locator(f"#fights-viewable li[data-fight='{fight_id}']")
    expect(row).to_contain_text("yours")
    expect(page.locator(f"#fights-team li[data-fight='{fight_id}']")).to_contain_text("won")
    expect(page.locator("#fights-team-pick")).to_have_value(page.locator("#fights-team-pick option").first.get_attribute("value"))
    row.locator("a").click()
    expect(page.locator("#stage")).to_have_attribute("data-link", "open", timeout=SLOW)
    faster(page)
    expect(page.locator("#stage")).to_have_attribute("data-over", "true", timeout=SLOW)


def test_a_visitor_sees_only_the_public_part(page, live_server):
    start(page, live_server)
    page.context.clear_cookies()
    page.goto("/fights/")
    expect(page.locator("#fights-public li")).to_have_count(1)
    expect(page.locator("#fights-viewable-card")).to_have_count(0)
    expect(page.locator("#fights-team-card")).to_have_count(0)


def test_a_private_fight_is_not_there_for_a_stranger_and_the_server_says_so(page, live_server, console):
    console.allow("status of 404")  # the browser logs the refusals itself
    _id, guid = start(page, live_server, public=False)
    page.context.clear_cookies()
    page.goto(f"/fights/?watch={guid}")
    expect(page.locator("#fights-problem")).to_have_text("there's no such fight")
    page.goto("/fights/")
    page.locator("[data-kind='pve_underdog']").click()
    expect(page.locator("#fights-problem")).to_have_text("there's no fight to show yet")


def fighter(party, name, hp=10):
    return {"party": party, "group": 0, "character": 0, "name": name, "alive": True, "fled": False, "resources": {"HP": [hp, 10]}, "statuses": [], "yours": False, "committed": False}


def test_a_party_that_joined_is_on_the_stage_when_the_replay_reaches_it(page, live_server):
    """The events only say a party joined; the replay carries the fight as it stood just after, which the scene picks up from."""
    start(page, live_server)  # (the page needs a server and a game; this replay is made up)
    begun = {"id": 99, "guid": "0" * 32, "round": 1, "over": False, "vital": "HP", "fighters": [fighter(0, "Aria"), fighter(1, "Slime")]}
    joined = {**begun, "fighters": [*begun["fighters"], fighter(2, "Bryn")]}
    ended = {**joined, "over": True, "round": 2}
    rounds = [{"round": 1, "events": [["PartyJoined", [2, {}, {}]], ["Turn", [2, 0, 0]], ["Attack", []], ["Damage", [1, 0, 0, 10, False]], ["Died", [1, 0, 0]], ["FightOver", []]]}]
    page.route("**/api/public/fights/*/replay", lambda route: route.fulfill(json={"fight": ended, "start": begun, "joins": {"1": joined}, "rounds": rounds}))
    page.goto(f"/fights/?watch={'0' * 32}")
    stage = page.locator("#stage")
    expect(stage).to_have_attribute("data-link", "open", timeout=SLOW)
    faster(page)
    expect(stage).to_have_attribute("data-over", "true", timeout=SLOW)
    expect(page.locator(".fighter")).to_have_count(3)
    expect(page.locator("[data-fighter='2.0.0']")).to_contain_text("Bryn")


def test_a_team_keeps_a_fight_by_hand_and_lets_it_go(page, live_server):
    fight_id, _guid = start(page, live_server)
    page.goto("/fights/")
    row = page.locator(f"#fights-team li[data-fight='{fight_id}']")
    expect(row).to_have_attribute("data-kept", "false")
    expect(page.locator("#fights-kept-count")).to_contain_text("Kept by hand: 0 of")
    row.get_by_role("button", name=f"Keep fight {fight_id}").click()
    expect(row).to_have_attribute("data-kept", "true")
    expect(page.locator("#fights-kept-count")).to_contain_text("Kept by hand: 1 of")
    page.reload()  # what is shown is the server's, so it is still kept
    expect(page.locator(f"#fights-team li[data-fight='{fight_id}']")).to_have_attribute("data-kept", "true")
    expect(page.locator("#fights-kept-count")).to_contain_text("Kept by hand: 1 of")
    page.get_by_role("button", name=f"Let go of fight {fight_id}").click()
    expect(page.locator(f"#fights-team li[data-fight='{fight_id}']")).to_have_attribute("data-kept", "false")
    expect(page.locator("#fights-kept-count")).to_contain_text("Kept by hand: 0 of")


def test_the_teams_best_fights_are_listed_as_the_server_has_them(page, live_server):
    fight_id, _guid = start(page, live_server)
    team = Player(page, "Mike").get("/api/teams")[0]["id"]
    held = {kind: tag for kind, tag in Player(page, "Mike").get(f"/api/teams/{team}/fight-tags")["tags"].items() if tag}
    page.goto("/fights/")
    expect(page.locator("#fights-kept-count")).to_be_visible()
    if held:
        rows = page.locator("#fights-tag-list li")
        expect(rows).to_have_count(len(held))
        for kind, tag in held.items():
            expect(page.locator(f"#fights-tag-list li[data-kind='{kind}'] a")).to_have_attribute("href", f"?fight={tag['fight_id']}")
    else:
        expect(page.locator("#fights-tags-none")).to_be_visible()


def test_a_second_team_has_its_own_tags_and_kept_fights(page, live_server):
    fight_id, _guid = start(page, live_server)
    Player(page, "Mike").post("/api/teams", {"name": "Beta", "heroes": [{"name": "Bryn", "job": "fighter"}]})
    page.goto("/fights/")
    page.locator("#fights-team-pick").select_option(label="Beta")
    expect(page.locator("#fights-team-none")).to_be_visible()  # Beta has fought nothing, so none of Alpha's fights show
    expect(page.locator(f"#fights-team li[data-fight='{fight_id}']")).to_have_count(0)
    expect(page.locator("#fights-kept-count")).to_contain_text("Kept by hand: 0 of")

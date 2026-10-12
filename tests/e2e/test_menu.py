"""The command menu on the fight screen, in a real browser: the player picks what their heroes do (attack, skill, spell, item, defend,
flee) and whom it is aimed at, and the page sends it through the commands call, so a fight is played without the API. The server decides
what a hero may do (the fight view's ``menu``) and the page only offers it; these tests teach the example's fighters a skill, three spells
and a potion, since the example game has none yet. The heroes fight a slime in the marsh of the hub (see test_fight)."""

from pathlib import Path

from playwright.sync_api import expect
from sqlalchemy import select

import terraforma.example
from terraforma.content.loader import load_content
from terraforma.example import GAME
from terraforma.heroes import inventory
from terraforma.heroes.models import Hero
from terraforma.seed import load_seed

from .test_fight import SLOW, faster, settled, sure_encounters, view_of
from .test_heroes import make_team, open_game, tab
from .test_map import tap

ABILITIES = [
    {"key": "slash", "name": "Slash", "kind": "skill", "effect": {"effect": "hurt", "base": 2}},
    {"key": "mend", "name": "Mend", "kind": "spell", "mp_cost": 2, "effect": {"effect": "heal", "base": 5}},
    {"key": "quake", "name": "Quake", "kind": "spell", "mp_cost": 1, "effect": {"effect": "hurt", "base": 1, "targets": "all_enemies"}},
    {"key": "meteor", "name": "Meteor", "kind": "spell", "mp_cost": 99, "effect": {"effect": "hurt", "base": 50}},
]


async def teach(db):
    """The example's content with a skill, three spells and some MP for the fighter, and a pack with potions for every hero."""
    seed = load_seed(Path(terraforma.example.__file__).parent / "seed")
    seed["abilities"] = ABILITIES
    for job in seed["jobs"]:
        job["abilities"] = [{"ability": each["key"], "level": 1} for each in ABILITIES]
        job["stat_growth"] = {**job["stat_growth"], "MP": 8}
    rules = GAME.rules
    await load_content(db, seed, rules.stats, rules.resource_names, rules.drop_chance_scale)


async def give_potions(db):
    for hero in (await db.scalars(select(Hero))).all():
        await inventory.add_item(db, hero, "potion", 2)


def taught_fight(page, live_server, heroes=(("Aria", "fighter"),)):
    """A fight in the marsh for a team of $heroes who know the skill and spells and carry potions. Returns the fight's number."""
    live_server.in_db(teach)
    open_game(page, live_server)
    make_team(page, "Alpha", heroes)
    tab(page, "Party")
    page.locator("#party-play").click()
    expect(page.locator("#stage")).to_have_attribute("data-map", "hub")
    live_server.in_db(give_potions)
    live_server.in_db(sure_encounters)
    page.wait_for_load_state("networkidle")  # (the party panel's own calls are done before the fight takes the stage)
    tap(page, 1, 7)
    expect(page.locator("#stage")).to_have_attribute("data-scene", "fight", timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-link", "open", timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-menu", "commands", timeout=SLOW)
    return int(page.locator("#stage").get_attribute("data-fight"))


def waits(page, hero):
    expect(page.locator("#stage")).to_have_attribute("data-hero", hero)


def committed(page, fight):
    return [each["committed"] for each in view_of(page, fight)["fighters"] if each["yours"]]


def round_played(page, before):
    expect(page.locator("#stage")).to_have_attribute("data-round", str(before + 1), timeout=SLOW)
    settled(page)


def menu(page, name):
    return page.locator(f"#fight-menu [data-command='{name}']")


def choice(page, text):
    return page.locator("#fight-menu .menu-choice", has_text=text)


FOES = """async (aim) => {
  const { FightModel } = await import('/client/js/fight-model.js');
  const fighter = (party, character, alive, yours = false) => ({ party, group: 0, character, name: `F${party}${character}`, alive, fled: false, resources: { HP: [alive ? 5 : 0, 9] }, statuses: [], yours, committed: false });
  const view = { id: 1, round: 1, over: false, vital: 'HP', deadline: null,
    fighters: [fighter(0, 0, true, true), fighter(0, 1, true), fighter(0, 2, false), fighter(1, 0, true), fighter(1, 1, false), fighter(2, 0, true)] };
  const model = new FightModel(view);
  model.fighter('2.0.0').fled = true;
  return model.targets('0.0.0', aim);
}"""


# --- who can be aimed at ----------------------------------------------------------------------------------------------

def test_the_model_offers_enemies_allies_or_the_fallen_to_aim_at(page, live_server):
    page.goto("/")
    enemies, allies, downed, none = (page.evaluate(FOES, aim) for aim in ("enemy", "ally", "downed", "none"))
    assert enemies == ["1.0.0"], "the standing members of other parties, not the fallen, and not one that has left"
    assert allies == ["0.0.0", "0.0.1"], "the standing members of the actor's own party, itself included"
    assert downed == ["0.0.2"]
    assert none == []


NAMES = """async () => {
  const { FightModel } = await import('/client/js/fight-model.js');
  const hero = { party: 0, group: 0, character: 0, name: 'Aria', alive: true, fled: false, resources: { HP: [9, 9] }, statuses: [], yours: true, committed: false,
    menu: { abilities: [{ key: 'slash', name: 'Slash' }, { key: 'mend', name: 'Mend' }], items: [{ key: 'potion', name: 'Potion' }] } };
  const foe = { party: 1, group: 0, character: 0, name: 'Slime', alive: true, fled: false, resources: { HP: [9, 9] }, statuses: [], yours: false, committed: false };
  const model = new FightModel({ id: 1, round: 1, over: false, vital: 'HP', deadline: null, fighters: [hero, foe] });
  const said = [['Defend', [1, 0, 0]], ['Turn', [0, 0, 0]], ['Skill', ['slash']], ['Spell', ['mend']], ['Item', ['potion']], ['Skill', ['unknown']]].map((event) => model.apply(event).text);
  model.apply(['Turn', [1, 0, 0]]);
  return [said, model.apply(['Skill', ['bite']]).text];
}"""


def test_the_log_says_a_heros_abilities_and_items_by_name_and_a_defender_by_its_address(page, live_server):
    page.goto("/")
    said, theirs = page.evaluate(NAMES)
    assert said[0] == ["fight.defend", {"name": "Slime"}], "told for the fighter whose address it carries, not whoever acted last"
    assert said[2:] == [["fight.skill", {"name": "Aria", "what": "Slash"}], ["fight.spell", {"name": "Aria", "what": "Mend"}], ["fight.item", {"name": "Aria", "what": "Potion"}], ["fight.skill", {"name": "Aria", "what": "unknown"}]]
    assert theirs == ["fight.skill", {"name": "Slime", "what": "bite"}], "the other side's abilities are only known by their keys"


# --- the menu ---------------------------------------------------------------------------------------------------------

def test_the_menu_offers_what_the_hero_can_do_and_says_whose_turn_it_is(page, live_server):
    taught_fight(page, live_server)
    stage = page.locator("#stage")
    waits(page, "0.0.0")
    expect(page.locator("#fight-menu")).to_be_visible()
    expect(page.locator("#fight-menu-hero")).to_have_text("Aria")
    for name in ("attack_left", "skill", "spell", "item", "defend", "flee"):
        expect(menu(page, name)).to_be_visible()
    expect(stage).to_have_attribute("data-menu", "commands")


def test_defend_is_sent_at_once_and_the_round_plays(page, live_server):
    fight = taught_fight(page, live_server)
    menu(page, "defend").click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria defends")
    assert view_of(page, fight)["round"] == 2


def test_attack_asks_for_a_target_among_the_enemies_and_sends_it(page, live_server):
    taught_fight(page, live_server)
    menu(page, "attack_left").click()
    expect(page.locator("#stage")).to_have_attribute("data-menu", "target")
    expect(page.locator(".fighter[data-side='right'][data-targetable='true']").first).to_be_visible()
    expect(page.locator(".fighter[data-side='left'][data-targetable='true']")).to_have_count(0)
    expect(page.locator("#fight-menu-prompt")).to_have_text("Choose a target")
    page.locator(".fighter[data-targetable='true']").first.click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria attacks")


def test_back_leaves_a_choice_and_nothing_is_sent(page, live_server):
    fight = taught_fight(page, live_server)
    menu(page, "skill").click()
    expect(page.locator("#stage")).to_have_attribute("data-menu", "list")
    page.locator("#menu-back").click()
    expect(page.locator("#stage")).to_have_attribute("data-menu", "commands")
    menu(page, "attack_left").click()
    page.keyboard.press("Escape")
    expect(page.locator("#stage")).to_have_attribute("data-menu", "commands")
    expect(page.locator(".fighter[data-targetable='true']")).to_have_count(0)
    assert committed(page, fight) == [False]


def test_a_skill_is_chosen_from_the_heros_list_and_aimed(page, live_server):
    taught_fight(page, live_server)
    menu(page, "skill").click()
    expect(choice(page, "Slash")).to_be_visible()
    expect(page.locator("#fight-menu .menu-choice")).to_have_count(1)  # (the spells are on their own list)
    choice(page, "Slash").click()
    page.locator(".fighter[data-targetable='true']").first.click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria uses Slash")


def test_a_spell_that_heals_is_aimed_at_the_heros_own_side_and_one_that_costs_too_much_is_not_offered_as_a_choice(page, live_server):
    taught_fight(page, live_server)
    menu(page, "spell").click()
    expect(choice(page, "Meteor")).to_be_disabled()
    expect(choice(page, "Meteor")).to_contain_text("99 MP")
    choice(page, "Mend").click()
    expect(page.locator(".fighter[data-targetable='true']")).to_have_count(1)
    expect(page.locator(".fighter[data-side='left'][data-targetable='true']")).to_have_count(1)
    page.locator(".fighter[data-fighter='0.0.0']").click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria casts Mend")


def test_a_spell_that_names_its_own_targets_is_sent_without_asking_for_one(page, live_server):
    taught_fight(page, live_server)
    menu(page, "spell").click()
    choice(page, "Quake").click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria casts Quake")


def test_an_item_is_chosen_with_how_many_are_left(page, live_server):
    taught_fight(page, live_server)
    menu(page, "item").click()
    expect(page.locator("#fight-menu .menu-choice")).to_have_count(1)  # (only the potion: nothing else carried does anything when used)
    expect(choice(page, "Potion")).to_contain_text("2")
    choice(page, "Potion").click()
    page.locator(".fighter[data-fighter='0.0.0']").click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria uses Potion")


def test_flee_is_sent_at_once(page, live_server):
    taught_fight(page, live_server)
    menu(page, "flee").click()
    expect(page.locator("#fight-log")).to_contain_text("Aria tries to get away", timeout=SLOW)


def test_the_keyboard_works_the_menu(page, live_server):
    taught_fight(page, live_server)
    menu(page, "attack_left").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#stage")).to_have_attribute("data-menu", "target")
    foe = page.locator(".fighter[data-targetable='true']").first
    expect(foe).to_have_attribute("role", "button")
    foe.focus()
    page.keyboard.press("Enter")
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria attacks")


# --- more than one hero -----------------------------------------------------------------------------------------------

def test_each_hero_is_commanded_in_turn_and_the_round_plays_when_the_last_has_chosen(page, live_server):
    fight = taught_fight(page, live_server, (("Aria", "fighter"), ("Bo", "scout")))
    waits(page, "0.0.0")
    menu(page, "defend").click()
    waits(page, "0.0.1")
    expect(page.locator("#fight-menu-hero")).to_have_text("Bo")
    assert committed(page, fight) == [True, False]
    expect(page.locator(".fighter[data-fighter='0.0.0']")).to_have_attribute("data-committed", "true")
    menu(page, "defend").click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Bo defends")


def test_a_hero_that_has_chosen_can_choose_again_before_the_round_plays(page, live_server):
    fight = taught_fight(page, live_server, (("Aria", "fighter"), ("Bo", "scout")))
    menu(page, "defend").click()
    waits(page, "0.0.1")
    page.locator(".fighter[data-fighter='0.0.0']").click()  # (an own hero's card picks who is being commanded)
    waits(page, "0.0.0")
    menu(page, "attack_left").click()
    page.locator(".fighter[data-side='right'][data-targetable='true']").first.click()
    waits(page, "0.0.1")
    assert committed(page, fight) == [True, False]
    menu(page, "defend").click()
    round_played(page, 1)
    expect(page.locator("#fight-log")).to_contain_text("Aria attacks")


# --- when the menu is not there ---------------------------------------------------------------------------------------

def test_the_menu_is_hidden_while_a_round_is_played_and_back_for_the_next(page, live_server):
    taught_fight(page, live_server)
    menu(page, "defend").click()
    expect(page.locator("#stage")).to_have_attribute("data-playing", "true", timeout=SLOW)
    expect(page.locator("#fight-menu")).to_be_hidden()
    settled(page)
    expect(page.locator("#fight-menu")).to_be_visible()
    expect(page.locator("#stage")).to_have_attribute("data-menu", "commands")


def test_a_finished_fight_has_no_menu_only_the_way_out(page, live_server):
    fight = taught_fight(page, live_server)
    faster(page)
    for _ in range(60):
        if view_of(page, fight)["over"]:
            break
        menu(page, "attack_left").click()
        page.locator(".fighter[data-targetable='true']").first.click()
        before = int(page.locator("#stage").get_attribute("data-round"))
        expect(page.locator("#stage")).not_to_have_attribute("data-round", str(before), timeout=SLOW)
        settled(page)
    else:
        raise AssertionError("the fight never ended")
    expect(page.locator("#stage")).to_have_attribute("data-over", "true", timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-menu", "off")
    expect(page.locator("#fight-menu")).to_be_hidden()
    expect(page.locator("#fight-leave")).to_be_visible()


def test_a_page_opened_in_the_middle_of_a_fight_shows_the_menu_again(page, live_server):
    fight = taught_fight(page, live_server)
    page.reload()
    expect(page.locator("#stage")).to_have_attribute("data-scene", "fight", timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-menu", "commands", timeout=SLOW)
    assert int(page.locator("#stage").get_attribute("data-fight")) == fight


def test_a_hero_who_already_chose_before_the_page_opened_is_not_asked_again(page, live_server):
    fight = taught_fight(page, live_server, (("Aria", "fighter"), ("Bo", "scout")))
    menu(page, "defend").click()
    waits(page, "0.0.1")
    page.reload()
    expect(page.locator("#stage")).to_have_attribute("data-menu", "commands", timeout=SLOW)
    waits(page, "0.0.1")
    assert committed(page, fight) == [True, False]

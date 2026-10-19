"""The example game's content: a small roster the engine accepts, plays and draws.

The seed's jobs, abilities, statuses, items, monsters and personalities are checked as a game's seed is, load into a
database, and fight each other under the engine's rules and AI. The assets are checked against the license record
(``assets/ASSETS.md``), and every personality's pictures against the sheets, color maps and animation files on disk.
"""

import json
import re
import struct

import pytest
from sqlalchemy import select

from terraforma.animations import check_animations
from terraforma.content import models
from terraforma.content.loader import load_content
from terraforma.content.schema import ArtRef, ContentError, check_seed
from terraforma.example import GAME
from terraforma.fights import ai, resolve
from terraforma.fights.build import monster_fighter
from terraforma.fights.combatant import Combatant
from terraforma.fights.events import EventType
from terraforma.fights.content import ability_spec, item_spec, status_spec
from terraforma.fights.fight import build_fight
from terraforma.fights.gear import EquipOutcome
from terraforma.fights.rules import Rules
from terraforma.npcs.script import tags
from terraforma.seed import load_seed
from terraforma.world.rng import WorldRng

pytestmark = [pytest.mark.anyio, pytest.mark.platform]

ASSETS = GAME.assets_dir
RULES = Rules()
JOBS = ("fighter", "scout", "mage", "cleric")
#: What a fight sheet and a map sheet must be able to play (the animation names are in the README, "Animations").
FIGHT_NAMES = ("base", "hit", "die", "flee", "attack_close", "skill", "spell", "item")
MAP_NAMES = tuple(f"{what}_{direction}" for what in ("stand", "move") for direction in ("up", "down", "left", "right"))


def seed():
    return load_seed(GAME.seed_dir)


def checked():
    return check_seed(seed(), RULES.stats, RULES.resource_names)


def by_key(rows):
    return {row.key: row for row in rows}


def animation_names(sheet_name):
    data = json.loads((ASSETS / f"{sheet_name}.anim.json").read_text())
    return set(data["animations"]) | set(data.get("aliases", {}))


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


# --- the roster -----------------------------------------------------------------------------------------------------------

def test_the_seed_passes_the_engines_checks():
    content = checked()
    assert all(content[kind] for kind in ("abilities", "items", "jobs", "monsters", "personalities", "statuses", "drop_tables"))


def test_four_jobs_each_grow_stats_and_learn_abilities_as_they_level():
    jobs = by_key(checked()["jobs"])
    assert set(JOBS) <= set(jobs)
    for key in JOBS:
        job = jobs[key]
        assert job.stat_growth["HP"] > 0 and job.abilities, f"{key} needs growth and at least one ability"
        assert job.abilities[0].level >= 1
    assert jobs["fighter"].stat_growth["Strength"] > jobs["mage"].stat_growth["Strength"], "fighters hit harder than mages"
    assert jobs["mage"].stat_growth["Power"] > jobs["fighter"].stat_growth["Power"], "mages cast better than fighters"
    assert jobs["cleric"].stat_growth["Resistance"] > jobs["fighter"].stat_growth["Resistance"]
    assert jobs["scout"].stat_growth["Speed"] >= max(job.stat_growth["Speed"] for job in jobs.values())
    assert max(each.level for job in jobs.values() for each in job.abilities) > 1, "something is learned after level 1"


def test_abilities_cover_attacks_healing_status_and_revival_as_spells_and_skills():
    abilities = checked()["abilities"]
    assert {ability.kind for ability in abilities} == {"spell", "skill"}
    effects = {ability.effect.effect for ability in abilities}
    assert {"hurt", "heal", "cause_bad_status", "cause_good_status", "remove_bad_status", "revive"} <= effects
    assert {ability.effect.targets for ability in abilities} >= {"individual", "group"}
    assert all(ability.description for ability in abilities), "the Heroes panel shows these"
    assert all(ability.mp_cost > 0 for ability in abilities if ability.kind == "spell")


def test_every_ability_and_status_is_used_by_someone():
    content = checked()
    taught = {each.ability for job in content["jobs"] for each in job.abilities} | {key for monster in content["monsters"] for key in monster.abilities}
    assert taught == {ability.key for ability in content["abilities"]}
    placed = {ability.effect.status for ability in content["abilities"]} | {item.use_effect.status for item in content["items"] if item.use_effect}
    assert placed - {None} == {status.key for status in content["statuses"]}


def test_the_statuses_show_a_tick_a_stat_and_a_damage_modifier():
    statuses = by_key(checked()["statuses"])
    assert {"good", "bad"} == {status.kind for status in statuses.values()}
    assert any(status.ticks for status in statuses.values()) and any(status.modifiers for status in statuses.values())
    assert any(modifier.kind == "stat" for status in statuses.values() for modifier in status.modifiers)
    assert any(modifier.kind == "damage_taken" for status in statuses.values() for modifier in status.modifiers)
    assert any(tick.action == "skip_turn" for status in statuses.values() for tick in status.ticks)


def test_the_items_cover_every_slot_and_every_way_to_use_one():
    items = checked()["items"]
    worn = {slot for item in items for slot in item.equip_slots or ()}
    assert {"hand", "arm", "head", "body", "back", "feet", "ammo", "lhand", "rhand"} <= worn
    assert {item.use_effect.effect for item in items if item.use_effect} >= {"heal", "restore_mp", "remove_bad_status", "revive"}
    assert any(item.equip_slots == ["lhand", "rhand"] for item in items), "a two-handed weapon"
    bow = next(item for item in items if item.attack and item.attack.ammo_type)
    arrows = [item for item in items if item.equip_slots == ["ammo"] and item.attack and item.attack.ammo_type == bow.attack.ammo_type]
    assert arrows, "a bow needs ammunition it can take"
    assert all(item.description for item in items), "the Pack panel shows these"


def test_the_example_still_has_the_herb_and_potion_its_tests_and_shop_depend_on():
    items = by_key(checked()["items"])
    assert items["herb"].price == 12 and items["potion"].price == 40


def test_every_item_can_be_found_by_a_player():
    content = checked()
    sold = {parts[i] for npc in content["npcs"] for parts in tags(npc.dialog) if parts[0] == "vend" for i in range(1, len(parts), 2)}
    tables = by_key(content["drop_tables"])
    dropped = {entry.item for table in tables.values() for entry in table.entries}
    carried = {key for monster in content["monsters"] for key in (*monster.items, *monster.equipment)}
    gear_for_monsters = {item.key for item in content["items"] if item.price == 0}
    for item in content["items"]:
        assert item.key in sold | dropped or item.key in gear_for_monsters, f"{item.key} is neither sold, dropped nor monster gear"
    assert gear_for_monsters <= carried, "monster gear belongs to a monster"
    assert "ether" in sold and "antidote" in sold, "the keeper sells the consumables a new party needs"


def test_the_monsters_are_a_spread_of_strengths_each_met_in_the_world():
    content = checked()
    monsters = by_key(content["monsters"])
    assert len(monsters) >= 6 and "slime" in monsters
    hp = sorted(monster.stats["HP"] for monster in monsters.values())
    assert hp[-1] >= 2 * hp[0], "from a bat to something that takes a party"
    assert len({monster.ai.action for monster in monsters.values()}) >= 3, "not every monster thinks alike"
    assert any(monster.abilities for monster in monsters.values()) and any(monster.equipment for monster in monsters.values())
    met = {name for each in content["maps"] for zone in each.zones for encounter in zone.encounters for name in encounter.monsters}
    assert met == set(monsters), "a monster no zone meets is dead content"
    for monster in monsters.values():
        assert monster.xp_reward > 0 and monster.gold_reward > 0 and monster.stats["HP"] > 0 and monster.stats["Speed"] > 0
        assert monster.drops, f"{monster.key} leaves something behind"


def test_the_marsh_the_e2e_tests_walk_into_keeps_meeting_only_slimes():
    hub = by_key(checked()["maps"])["hub"]
    marsh = next(zone for zone in hub.zones if zone.name == "marsh")
    assert {name for encounter in marsh.encounters for name in encounter.monsters} == {"slime"}


# --- personalities and pictures -----------------------------------------------------------------------------------------

def test_every_job_has_a_personality_of_its_name_with_a_fight_picture_and_a_map_picture():
    content = checked()
    personalities = by_key(content["personalities"])
    for key in JOBS:
        personality = personalities[key]
        assert isinstance(personality.fight_art, ArtRef) and isinstance(personality.map_art, ArtRef)
        assert personality.fight_art.sheet != personality.map_art.sheet, "a hero's fight art and map art are separate sheets"
        assert personality.fight_art.animations and personality.map_art.animations


def test_every_monster_has_a_personality_with_a_fight_picture():
    content = checked()
    personalities = by_key(content["personalities"])
    for monster in content["monsters"]:
        assert personalities[monster.personality].fight_art is not None, monster.key


def references(content):
    for personality in content["personalities"]:
        for field in ("fight_art", "map_art"):
            art = getattr(personality, field)
            if art is not None:
                yield personality.key, field, art


def test_every_picture_is_a_sheet_that_exists_with_the_frames_colors_and_animations_it_names():
    for key, field, art in references(checked()):
        where = f"{key}.{field}"
        sheet = json.loads((ASSETS / f"{art.sheet}.sheet.json").read_text())
        width, height = png_size(ASSETS / f"{art.sheet}.png")
        assert (width, height) == (sheet["frame"][0] * sheet["grid"][0], sheet["frame"][1] * sheet["grid"][1]), where
        assert sheet["frames"] <= sheet["grid"][0] * sheet["grid"][1] and art.frame < sheet["frames"], where
        if sheet["alpha"]:
            assert png_size(ASSETS / f"{art.sheet}.alpha.png") == (width, height), where
        if art.colors:
            colors = json.loads((ASSETS / f"{art.colors}.colors.json").read_text())
            assert colors and set(colors) <= set(sheet["palette"]), f"{where}: a color map changes colors the sheet has"
        check_animations(json.loads((ASSETS / f"{art.animations}.anim.json").read_text()), frames=sheet["frames"])


def test_fight_sheets_play_the_fight_names_and_map_sheets_the_walking_names():
    for key, field, art in references(checked()):
        names = animation_names(art.animations)
        wanted = FIGHT_NAMES if field == "fight_art" else MAP_NAMES
        if field == "fight_art" and key not in JOBS:
            wanted = ("base", "hit", "die", "attack_close")
        assert set(wanted) <= names, f"{key}.{field} lacks {sorted(set(wanted) - names)}"


def test_a_hero_is_one_pair_of_sheets_recolored_per_job():
    personalities = by_key(checked()["personalities"])
    fights = {personalities[key].fight_art.sheet for key in JOBS}
    maps = {personalities[key].map_art.sheet for key in JOBS}
    assert len(fights) == 1 and len(maps) == 1, "the jobs share a body"
    assert len({personalities[key].fight_art.colors for key in JOBS}) == len(JOBS), "and tell apart by color map"
    assert len({personalities[key].map_art.colors for key in JOBS}) == len(JOBS)


def test_every_asset_file_is_recorded_with_its_source_and_a_license_the_agpl_accepts():
    record = (ASSETS / "ASSETS.md").read_text()
    rows = [line.split("|")[1:-1] for line in record.splitlines() if line.startswith("|") and not line.startswith("|---") and "File" not in line]
    named = {name for files, _, _ in rows for name in (part.strip(" `") for part in files.split(",")) if name}
    on_disk = {path.relative_to(ASSETS).as_posix() for path in ASSETS.rglob("*") if path.is_file() and path.name != "ASSETS.md"}
    assert on_disk <= named, f"not in ASSETS.md: {sorted(on_disk - named)}"
    assert named <= on_disk, f"ASSETS.md lists files that are not there: {sorted(named - on_disk)}"
    for files, source, license_ in rows:
        assert source.strip() and license_.strip(), files
        assert not re.search(r"unknown|non-?commercial|no-?derivatives|by-n[cd]|sa[- ][123]\.|sa[- ]1", license_.lower()), files


def test_the_server_accepts_the_games_animation_files():
    from terraforma.client import check_game_client

    check_game_client(GAME)


# --- the personality format -----------------------------------------------------------------------------------------------

def personality(**fields):
    return {"personalities": [{"key": "elf", "name": "Elf", **fields}]}


def test_a_personality_may_carry_a_fight_picture_and_a_map_picture_each_a_sheet_reference_or_a_file():
    row = check_seed(personality(fight_art={"sheet": "elf_fight", "colors": "green", "animations": "elf_fight"}, map_art="elf.svg"))["personalities"][0]
    assert row.fight_art == ArtRef(sheet="elf_fight", colors="green", animations="elf_fight") and row.map_art == "elf.svg"
    plain = check_seed(personality())["personalities"][0]
    assert plain.fight_art is None and plain.map_art is None


@pytest.mark.parametrize("field", ["fight_art", "map_art"])
@pytest.mark.parametrize("art", [{"sheet": "../elf"}, {"sheet": "elf", "extra": 1}, {"frame": 2}, "/etc/passwd", 7])
def test_a_bad_personality_picture_is_refused_naming_the_field(field, art):
    with pytest.raises(ContentError, match=f"personalities.json row 1 .*{field}"):
        check_seed(personality(**{field: art}))


async def test_loading_stores_a_personalitys_pictures_and_loading_again_changes_them(db):
    first = personality(fight_art={"sheet": "elf_fight", "animations": "elf_fight"}, map_art="elf.svg")
    await load_content(db, check_seed(first) and first)
    row = (await db.scalars(select(models.Personality))).one()
    assert row.fight_art == {"sheet": "elf_fight", "frame": 0, "colors": None, "animations": "elf_fight"} and row.map_art == "elf.svg"
    await load_content(db, personality())
    await db.refresh(row)
    assert row.fight_art is None and row.map_art is None


# --- the content in play ---------------------------------------------------------------------------------------------------

async def loaded(db):
    await load_content(db, seed(), RULES.stats, RULES.resource_names)
    await db.commit()


def play_out(fight, limit=120, seed_number=7):
    """Plays the fight round by round, every fighter chosen by the AI, from a fixed seed. Returns the rounds played and every event."""
    stream = WorldRng(seed_number).stream("example", "content")
    rounds, events = 0, []
    while not fight.over and rounds < limit:
        for address in fight.addresses():
            fighter = fight.get(address)
            if fighter.present(RULES):
                ai.commit(fighter, ai.choose_command(RULES, fight, address, stream))
        events += resolve.do_combat(fight, RULES, stream)
        rounds += 1
    return rounds, events


async def statuses_of(db):
    return {row.key: status_spec(row) for row in (await db.scalars(select(models.Status))).all()}


async def test_the_content_loads_and_every_monster_builds_into_a_fighter_with_its_gear_on(db):
    await loaded(db)
    for monster in (await db.scalars(select(models.Monster))).all():
        fighter = await monster_fighter(db, monster.key, RULES)
        assert fighter.base["HP"] == monster.stats["HP"] and fighter.monster == monster.key
        for key in monster.equipment:
            assert any(spec.key == key and index in fighter.equipment.values() for index, (spec, _) in enumerate(fighter.inventory)), f"{monster.key} wears {key}"


async def test_every_monster_fights_every_other_to_a_finish_or_a_round_limit_without_error(db):
    await loaded(db)
    keys = [monster.key for monster in (await db.scalars(select(models.Monster).order_by(models.Monster.key))).all()]
    statuses = await statuses_of(db)
    seen = set()
    for left in keys:
        for right in keys:
            sides = {0: {0: [await monster_fighter(db, left, RULES)]}, 1: {0: [await monster_fighter(db, right, RULES)]}}
            fight = build_fight(sides, statuses)
            rounds, events = play_out(fight)
            assert rounds <= 120 and any(event.type is EventType.DAMAGE for event in events), f"{left} against {right}: nobody was hurt"
            seen |= {(event.type, event.data[0]) for event in events if event.type in (EventType.SKILL, EventType.SPELL)}
    assert {key for _, key in seen} >= {"bite", "screech", "ember"}, "every monster ability gets used by the AI somewhere"


async def hero_of(db, job, level=3):
    """A hero of the job at $level in the starter kit the shop sells, as the fight sees it."""
    row = (await db.scalars(select(models.Job).where(models.Job.key == job))).one()
    stats = {stat: round(amount * level) for stat, amount in row.stat_growth.items()}
    abilities = {each["ability"]: await db.scalar(select(models.Ability).where(models.Ability.key == each["ability"])) for each in row.abilities if each["level"] <= level}
    kit = KITS[job]
    items = {key: await db.scalar(select(models.Item).where(models.Item.key == key)) for key in kit}
    hero = Combatant(
        name=job.title(), base=dict(stats), current={name: value for name, value in stats.items() if name in RULES.resource_names},
        abilities=[ability_spec(ability) for ability in abilities.values()],
        inventory=[[item_spec(item), 1] for item in items.values()],
        ai_action=ai.Action.NORMAL, ai_goal=ai.Goal.DESTRUCTOR, ai_target=ai.Aim.NORMAL,
    )
    for index, key in enumerate(kit):
        for side in (0, 1):
            if hero.equip(index, side).outcome is EquipOutcome.SUCCESS:
                break
    return hero


#: What each job wears and carries into the test fights (everything is in the keeper's stock).
KITS = {
    "fighter": ("short_sword", "wooden_shield", "leather_armor", "potion"),
    "scout": ("dagger", "leather_cap", "leather_boots", "antidote"),
    "mage": ("staff", "cloth_robe", "ether", "herb"),
    "cleric": ("mace", "cloth_robe", "herb", "antidote"),
}


async def test_a_party_of_one_of_each_job_beats_every_group_the_world_throws_at_it(db):
    await loaded(db)
    statuses = await statuses_of(db)
    groups = {tuple(encounter.monsters) for each in checked()["maps"] for zone in each.zones for encounter in zone.encounters}
    assert groups
    used = set()
    for group in sorted(groups):
        heroes = [await hero_of(db, job) for job in JOBS]
        foes = [await monster_fighter(db, key, RULES) for key in group]
        fight = build_fight({0: {0: heroes}, 1: {0: foes}}, statuses)
        rounds, events = play_out(fight)
        assert fight.over and rounds < 120, f"the fight against {group} never ended"
        assert any(hero.alive(RULES) for hero in heroes), f"the party lost to {group}"
        used.update(event.data[0] for event in events if event.type in (EventType.SKILL, EventType.SPELL))
    known = {each.ability for job in checked()["jobs"] for each in job.abilities if each.level <= 3}
    assert len(used & known) >= 4, f"the AI barely used the jobs' abilities (it used {sorted(used)})"

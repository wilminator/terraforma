"""NPC dialog text: checked when it is placed, and run one step at a time. No database needed."""

import pytest

from terraforma.npcs.script import MAX_TEXT, ScriptError, Who, advance, parse

pytestmark = pytest.mark.anyio

WHO = Who(frozenset({7}), 3)


async def unhandled(command, parts):
    return None


async def run(text, *answers, who=WHO, tag=unhandled, state=None, trees=None):
    """Runs the dialog, answering each prompt in turn: returns the frames (events and prompt) it went through. $trees maps a
    key to the text of a dialog tree it may include."""
    script = parse(text)

    async def load(key):
        if key not in (trees or {}):
            raise ScriptError(f"there is no dialog {key!r} to include")
        return parse(trees[key])

    pos, prompt, frames, pending, tree, stack = 0, None, [], list(answers), None, []
    while True:
        result = await advance(script, pos, prompt, pending.pop(0) if prompt is not None and pending else None, who, tag, state, tree, stack, load)
        frames.append(result)
        pos, prompt, tree, stack = result["pos"], result["prompt"], result["tree"], result["stack"]
        if prompt is None or not pending:
            return frames


def said(frames):
    return "".join(event["text"] for frame in frames for event in frame["events"] if event["type"] == "text")


# --- checking ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, problem", [
    ("Hi `jump,a", "unpaired backtick"),
    ("`nonsense`", "unknown tag"),
    ("`label`", "label tag takes 1 parameter"),
    ("`label,`", "needs a name"),
    ("`jump,a,b`", "jump tag takes 1 parameter"),
    ("`ack,now`", "ack tag takes 0 parameters"),
    ("`heal,1`", "heal tag takes 0 parameters"),
    ("`end,now`", "end tag takes 0 parameters"),
    ("`pause`", "pause tag takes 1 parameter"),
    ("`pause,soon`", "whole number"),
    ("`team,x,end`", "whole number"),
    ("`party,1`", "party tag takes 2 parameters"),
    ("`question`", "at least one prompt"),
    ("`question,Yes`", "at least one prompt"),
    ("`switch,Yes,end,No`", "complete prompt and label pairs"),
    ("`vend,sword`", "at least one item and price"),
    ("`vend,sword,cheap`", "whole number"),
    ("`hawk`", "margin"),
    ("`hawk,lots`", "whole number"),
    ("`hawk,50,sword,x`", "whole number"),
    ("`inn,10`", "inn tag takes 2 parameters"),
    ("`inn,ten,end`", "whole number"),
    ("`services,Rest,5`", "complete prompt, price and label triplets"),
    ("`services,Rest,five,end`", "whole number"),
    ("`resurrect,100`", "resurrect tag takes 2 parameters"),
    ("`have_item,potion,1,any`", "have_item tag takes 4 parameters"),
    ("`have_item,,1,any,end`", "needs an item"),
    ("`have_item,potion,0,any,end`", "at least 1"),
    ("`have_item,potion,x,any,end`", "whole number"),
    ("`have_item,potion,1,some,end`", "scope is one of any, lead, each"),
    ("`add_item,potion,1`", "add_item tag takes 3 parameters"),
    ("`remove_item,potion,1,end,end`", "remove_item tag takes 3 parameters"),
    ("`quests,hunt,1,ge,3,any`", "quests tag takes 6 parameters"),
    ("`quests,,1,ge,3,any,end`", "needs a category"),
    ("`quests,hunt,high,ge,3,any,end`", "level \\(or any\\)"),
    ("`quests,hunt,1,gte,3,any,end`", "comparison is one of eq, ne, lt, le, gt, ge"),
    ("`quests,hunt,1,ge,many,any,end`", "whole number"),
    ("`quests,hunt,1,ge,3,all,end`", "scope is one of any, lead, each"),
    ("`quest_marker,rats,ge,2,any`", "quest_marker tag takes 5 parameters"),
    ("`quest_marker,rats,more,2,any,end`", "comparison is one of"),
    ("`set_quest_marker,rats`", "set_quest_marker tag takes 2 parameters"),
    ("`set_quest_marker,rats,-1`", "whole number"),
    ("`set_quest_marker,rats,1000001`", "at most 1000000"),
    ("`quests,hunt,1,ge,12345678901,any,end`", "at most 9 digits"),
    ("`have_item,potion,1,any,nowhere`", "there is no label 'nowhere'"),
    ("`jump,nowhere`", "there is no label 'nowhere'"),
    ("`question,Yes,gone`", "there is no label 'gone'"),
    ("`question,Yes,end,gone`", "there is no label 'gone'"),
    ("`label,a``label,a`", "defined twice"),
])
def test_dialog_text_that_is_wrong_says_what_and_where(text, problem):
    with pytest.raises(ScriptError, match=problem):
        parse(text)


def test_the_end_label_and_an_empty_label_always_exist():
    assert parse("`jump,end``question,Yes,,No,end,end`") is not None


def test_every_tag_in_dragonstars_list_is_accepted():
    text = ("`label,top`Hello `` there`ack``pause,500``sound,hit``music,theme``mute``team,7,end``party,3,end``question,A,top,B,end,end`\n"
            "`switch,A,top,B,end``vend,sword,100,potion,5,end``hawk,50,potion,3,end``inn,10,end``services,Rest,10,top,Pray,20,end`"
            "`heal``recharge``select_team``resurrect,100,end``cure,50,end``uncurse,50,end`")
    assert set(parse(text).labels) == {"top"}


def test_the_item_and_quest_tags_are_accepted():
    text = ("`label,no``have_item,potion,2,each,no``have_item,potion,1,lead,end``have_item,potion,1,any,end``add_item,potion,3,no``remove_item,potion,1,no`"
            "`quests,hunt,2,ge,3,lead,no``quests,hunt,any,lt,10,each,no``quests,hunt,0,eq,0,any,no``quests,hunt,1,ne,1,any,no``quests,hunt,1,le,1,any,no``quests,hunt,1,gt,1,any,no`"
            "`quest_marker,rats,ge,2,any,no``set_quest_marker,rats,2``set_quest_marker,rats,0`")
    assert set(parse(text).labels) == {"no"}


def test_a_dialog_is_not_longer_than_the_limit():
    with pytest.raises(ScriptError, match="at most"):
        parse("a" * (MAX_TEXT + 1))


# --- running -------------------------------------------------------------------------------------------------------

async def test_plain_text_is_said_and_ends():
    (frame,) = await run("Welcome, traveler.")
    assert frame["events"] == [{"type": "text", "text": "Welcome, traveler."}] and frame["prompt"] is None


async def test_an_empty_tag_shows_a_backtick():
    assert said(await run("a``b")) == "a`b"


async def test_a_jump_goes_to_just_after_its_label():
    assert said(await run("one`jump,there`skipped`label,there`two")) == "onetwo"


async def test_running_past_the_last_text_and_the_end_label_both_leave():
    assert said(await run("a`jump,end`b")) == "a"


async def test_the_end_tag_leaves_the_dialog_where_it_stands():
    frames = await run("Goodbye.`end`This is never said.`ack`")
    assert said(frames) == "Goodbye." and frames[-1]["prompt"] is None and len(frames) == 1
    text = "`question,Leave,bye,Stay,stay`Going?`label,stay`Good.`jump,end``label,bye`Safe travels.`end`Unreachable."
    assert said(await run(text, 0)) == "Going?Safe travels."


async def test_a_dialog_that_goes_in_circles_is_cut_off():
    with pytest.raises(ScriptError, match="circles"):
        await run("`label,a``jump,a`")


async def test_team_and_party_tags_send_other_players_on():
    text = "`team,7,skip`mine`jump,end``label,skip``party,4,gone`not my party`label,gone`"
    assert said(await run(text)) == "mine"  # team 7 is theirs, so it reads on, and party 4 is not theirs
    assert said(await run(text, who=Who(frozenset({9}), 4))) == "not my party"


async def test_ack_waits_for_next():
    first, second = await run("Hello`ack`Goodbye", None)
    assert first["prompt"] == {"type": "ack"} and said([first]) == "Hello"
    assert second["prompt"] is None and said([second]) == "Goodbye"


async def test_a_question_asks_after_its_text_block_and_the_answer_jumps():
    text = "`question,Buy,buy,Leave,end`What will it be?\nlater`jump,end``label,buy`Here you go."
    asked, answered = await run(text, 0)
    assert said([asked]) == "What will it be?\n", "the text block after a question ends at the line break"
    assert asked["prompt"]["options"] == [{"text": "Buy", "label": "buy"}, {"text": "Leave", "label": "end"}]
    assert said([answered]) == "Here you go." and answered["prompt"] is None
    _asked, left = await run(text, 1)
    assert said([left]) == "" and left["prompt"] is None


async def test_cancelling_a_question_goes_to_its_cancel_label_or_on_with_the_text():
    with_cancel = "`question,Yes,end,gone`Sure?`jump,end``label,gone`Fine."
    _asked, frame = await run(with_cancel, None)
    assert said([frame]) == "Fine."
    without = "`question,Yes,end`Sure?\nAnd after."
    _asked, frame = await run(without, None)
    assert said([frame]) == "And after."


async def test_a_switch_cancel_goes_on_with_the_text():
    text = "`switch,Red,red,Blue,blue`Which?\nNeither then.`jump,end``label,red`R`jump,end``label,blue`B"
    _asked, frame = await run(text, None)
    assert said([frame]) == "Neither then."
    _asked, frame = await run(text, 1)
    assert said([frame]) == "B"


async def test_a_choice_at_the_very_end_is_still_asked():
    frames = await run("`question,Yes,end`")
    assert frames[-1]["prompt"]["kind"] == "question"


async def test_an_answer_that_is_not_an_option_is_refused():
    script = parse("`question,Yes,end`Sure?")
    asked = await advance(script, 0, None, None, WHO, unhandled)
    for bad in (1, 5):
        with pytest.raises(ScriptError, match="not one of the choices"):
            await advance(script, asked["pos"], asked["prompt"], bad, WHO, unhandled)
    ack = await advance(parse("`ack`"), 0, None, None, WHO, unhandled)
    with pytest.raises(ScriptError, match="only Next"):
        await advance(parse("`ack`"), ack["pos"], ack["prompt"], 0, WHO, unhandled)


async def test_cues_come_out_in_order_with_the_text():
    (frame,) = await run("a`sound,hit`b`music,theme``pause,250`c`mute`")
    assert frame["events"] == [
        {"type": "text", "text": "a"}, {"type": "sound", "clip": "hit"}, {"type": "text", "text": "b"},
        {"type": "music", "clip": "theme"}, {"type": "pause", "ms": 250}, {"type": "text", "text": "c"}, {"type": "mute"},
    ]


async def test_a_tag_the_game_handles_goes_where_it_says():
    seen = []

    async def game(command, parts):
        seen.append((command, parts))
        return {"heal": "", "recharge": "healed"}.get(command)

    frames = await run("`heal`a`recharge`skipped`label,healed`b", tag=game)
    assert said(frames) == "ab" and seen == [("heal", []), ("recharge", [])]


async def test_a_tag_the_game_does_not_handle_becomes_an_activity_for_the_browser():
    first, second = await run("`heal`Rest up.`resurrect,100,no`Back.`jump,end``label,no`No.", None)
    assert first["prompt"] == {"type": "activity", "command": "heal", "parts": [], "cancel": None}
    assert second["prompt"] == {"type": "activity", "command": "resurrect", "parts": ["100", "no"], "cancel": "no"}


async def test_an_activity_is_finished_with_next_and_its_cancel_label_takes_the_text_on():
    text = "`vend,sword,100,potion,5,done`Welcome.`jump,end``label,done`Come again."
    asked, finished = await run(text, None)
    assert said([asked]) == "Welcome."
    assert asked["prompt"] == {"type": "activity", "command": "vend", "parts": ["sword", "100", "potion", "5", "done"], "cancel": "done"}
    assert said([finished]) == "Come again."
    script = parse(text)
    with pytest.raises(ScriptError, match="finished with Next"):
        await advance(script, asked["pos"], asked["prompt"], 0, WHO, unhandled)


async def test_a_hawk_without_a_cancel_label_goes_on_with_the_text():
    asked, finished = await run("`hawk,50,potion,3`Selling?\nThanks.", None)
    assert asked["prompt"]["cancel"] is None and said([finished]) == "Thanks."


async def test_a_shop_the_game_handles_is_not_shown_to_the_browser():
    async def game(command, parts):
        return "" if command == "vend" else None

    frames = await run("`vend,sword,100`Welcome.\nBye.", tag=game)
    assert said(frames) == "Welcome.\nBye." and frames[-1]["prompt"] is None


async def test_an_inn_asks_yes_or_no_and_no_goes_to_its_label():
    text = "`inn,10,leave`A bed is 10 gold. Rest?`ack`Sleep well.`jump,end``label,leave`Another time."
    asked = (await run(text))[0]
    assert asked["prompt"]["kind"] == "inn" and [option["text"] for option in asked["prompt"]["options"]] == ["Yes", "No"]
    assert said(await run(text, 1)) == "A bed is 10 gold. Rest?Another time."
    assert said(await run(text, None)) == "A bed is 10 gold. Rest?Another time.", "cancelling is No"


async def test_yes_at_an_inn_is_the_games_to_handle():
    text = "`inn,10,leave`Rest?`ack`Sleep well.`jump,end``label,leave`No."
    frames = await run(text, 0)
    assert frames[1]["prompt"] == {"type": "activity", "command": "inn", "parts": ["10", "leave"], "cancel": None}

    async def game(command, parts):
        return "" if command == "inn" else None

    handled = await run(text, 0, tag=game)
    assert handled[1]["prompt"] == {"type": "ack"}
    assert said(handled) == "Rest?"


async def test_services_the_game_does_not_handle_are_never_free():
    asked = (await run("`services,Rest,10,rest,Pray,20,end`Which?\n`label,rest`Rested."))[0]
    assert asked["prompt"]["type"] == "activity" and asked["prompt"]["command"] == "services"
    assert "options" not in asked["prompt"], "a menu that picked a label would skip the charge"


# --- the tags that read the game's state -----------------------------------------------------------------------------------

async def test_a_state_tag_that_fails_jumps_to_its_label_and_one_that_holds_goes_on():
    asked = []

    async def state(command, parts):
        asked.append((command, parts))
        return "no" if command == "have_item" and parts[0] == "crown" else ""

    text = "`have_item,potion,1,any,no`has potion, `have_item,crown,1,each,no`has crown`jump,end``label,no`lacks crown"
    assert said(await run(text, state=state)) == "has potion, lacks crown"
    assert asked == [("have_item", ["potion", "1", "any", "no"]), ("have_item", ["crown", "1", "each", "no"])]


async def test_every_state_tag_goes_to_the_state_hook_with_its_parameters():
    seen = []

    async def state(command, parts):
        seen.append(command)
        return ""

    text = "`have_item,a,1,any,end``add_item,a,1,end``remove_item,a,1,end``quests,hunt,1,ge,2,lead,end``quest_marker,q,eq,1,each,end``set_quest_marker,q,1`done"
    assert said(await run(text, state=state)) == "done"
    assert seen == ["have_item", "add_item", "remove_item", "quests", "quest_marker", "set_quest_marker"]


async def test_a_dialog_that_reads_state_with_no_one_to_ask_is_refused():
    with pytest.raises(ScriptError, match="no one to ask"):
        await run("`have_item,a,1,any,end`")


def test_add_team_takes_an_optional_token_price():
    assert parse("`add_team``add_team,2`") is not None
    for bad in ("`add_team,0`", "`add_team,x`", "`add_team,1,2`"):
        with pytest.raises(ScriptError, match="add_team"):
            parse(bad)


# --- including other trees -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, problem", [
    ("`include`", "include tag takes 1 parameter"),
    ("`include,a,b`", "include tag takes 1 parameter"),
    ("`include,Quest One`", "the key is 1 to 64"),
    ("`include,`", "the key is 1 to 64"),
    ("`end_return,now`", "end_return tag takes 0 parameters"),
    ("`end_complete,now`", "end_complete tag takes 0 parameters"),
])
def test_an_include_and_the_new_endings_are_checked(text, problem):
    with pytest.raises(ScriptError, match=problem):
        parse(text)


def test_a_script_knows_the_trees_it_includes_once_each_in_order():
    assert parse("`include,b`x`include,a``include,b`").includes == ("b", "a")
    assert parse("plain").includes == ()


async def test_an_included_tree_that_runs_off_its_end_goes_back_to_the_caller():
    frames = await run("One.`include,two`Three.", trees={"two": "Two."})
    assert said(frames) == "One.Two.Three." and frames[-1]["prompt"] is None and frames[-1]["stack"] == []


async def test_end_return_goes_back_and_end_complete_ends_everything():
    trees = {"back": "In.`end_return`Never.", "over": "In.`end_complete`Never.", "plain": "In.`end`Never."}
    assert said(await run("A.`include,back`B.", trees=trees)) == "A.In.B."
    assert said(await run("A.`include,over`B.", trees=trees)) == "A.In."
    assert said(await run("A.`include,plain`B.", trees=trees)) == "A.In.", "end is end_complete"
    assert said(await run("A.`end_return`B.")) == "A.", "at the top, end_return ends the dialog"
    assert said(await run("A.`end_complete`B.")) == "A."


async def test_a_prompt_inside_an_included_tree_is_answered_there_and_then_the_caller_goes_on():
    trees = {"quest": "Take the quest?`question,Yes,yes,No,no`\n`label,yes`Good luck.`end_return``label,no`Maybe later."}
    text = "Board.`ack``include,quest`Back at the board.`ack`Bye."
    frames = await run(text, None, 0, None, trees=trees)
    assert [(frame["prompt"] or {}).get("type") for frame in frames] == ["ack", "choice", "ack", None]
    assert frames[1]["tree"] == "quest" and frames[1]["stack"] == [{"tree": None, "pos": text.index("Back at")}]
    assert said(frames) == "Board.Take the quest?\nGood luck.Back at the board.Bye."
    declined = await run(text, None, 1, None, trees=trees)
    assert said(declined).endswith("Maybe later.Back at the board.Bye."), "running off the end of the tree goes back too"


async def test_labels_belong_to_their_own_tree():
    trees = {"inner": "`label,x`Inner x.`jump,end`"}
    frames = await run("`include,inner``jump,x``label,x`Outer x.", trees=trees)
    assert said(frames) == "Inner x.Outer x."
    with pytest.raises(ScriptError, match="no label 'x'"):
        parse("`include,inner``jump,x`")  # (a jump never reaches into an included tree)


async def test_the_end_label_of_an_included_tree_goes_back():
    trees = {"quest": "Pick.`question,Leave,end`"}
    frames = await run("A.`include,quest`B.", 0, trees=trees)
    assert said(frames) == "A.Pick.B."


async def test_trees_can_include_trees_and_each_goes_back_to_the_one_that_included_it():
    trees = {"a": "A1.`include,b`A2.", "b": "B1.`include,c`B2.", "c": "C."}
    assert said(await run("`include,a`Z.", trees=trees)) == "A1.B1.C.B2.A2.Z."
    trees["c"] = "C.`end_complete`"
    assert said(await run("`include,a`Z.", trees=trees)) == "A1.B1.C."


async def test_a_tree_that_includes_itself_is_cut_off():
    with pytest.raises(ScriptError, match="deep"):
        await run("`include,loop`", trees={"loop": "x`include,loop`"})


async def test_a_tree_that_is_not_there_is_a_mistake_in_the_text():
    with pytest.raises(ScriptError, match="no dialog 'ghost'"):
        await run("`include,ghost`")
    with pytest.raises(ScriptError, match="nowhere to look it up"):
        await advance(parse("`include,ghost`"), 0, None, None, WHO, unhandled)


async def test_the_game_state_tags_work_inside_an_included_tree():
    async def state(command, parts):
        return "no" if command == "have_item" else ""

    trees = {"gate": "`have_item,key,1,any,no`Open.`end_return``label,no`Locked."}
    assert said(await run("`include,gate`After.", trees=trees, state=state)) == "Locked.After."


async def test_an_activity_the_game_deals_with_goes_on_in_the_included_tree():
    async def tag(command, parts):
        return "" if command == "vend" else None

    frames = await run("`include,shop`Back.", trees={"shop": "Buy?`vend,potion,5`Thanks."}, tag=tag)
    assert said(frames) == "Buy?Thanks.Back." and frames[-1]["prompt"] is None

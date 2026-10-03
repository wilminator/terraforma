"""Dialog text: what an NPC says, and the tags that steer it. A port of DragonStar's dialog language, run on the server.

A tag is quoted with a pair of backticks, and an empty tag (two backticks) shows one backtick. The first word of a tag is
its command, followed by comma separated parameters. Text outside tags is shown. A text block ends at a line break or a tag.

    label,name           names this place in the text (a jump goes to just after it)
    jump,label           go to the label
    team,id,label        the text that follows is for the teams whose id is given: any other player jumps to the label
    party,id,label       likewise for a party id
    question,prompt1,label1,prompt2,label2,...[,cancel]
                         after the text that follows, ask: the answer jumps to its label; an odd last parameter is where
                         cancelling goes, and without one cancelling goes on with the text
    switch,prompt1,label1,...   like question, but cancelling always goes on with the text
    vend,item1,price1,...[,cancel]         a shop menu after the text that follows (the market answers it)
    hawk,margin,item1,price1,...[,cancel]  a sell-back menu likewise
    shop,key[,cancel]    a shop the game stocks by the area's economy level (``market.hooks.Market``): buying and selling back
    inn,price,label      after the text that follows: Yes or No; No (or cancelling) jumps to the label, Yes lets the game rest the party
    services,prompt1,price1,label1,...  a menu of paid services (the game answers it)
    have_item,item,qty,scope,label    jump to the label unless the party has the item (at least qty, held by one hero)
    add_item,item,qty,label   put qty of an item in the talking hero's pack; jump to the label if it does not all fit
    remove_item,item,qty,label  take qty of an item from the talking hero; jump to the label if they do not have it
    quests,category,level,op,n,scope,label  jump to the label unless the quests the team completed compare true with n
    quest_marker,quest,op,value,scope,label  jump to the label unless the team's marker for the quest compares true with value
    set_quest_marker,quest,value   set the talking hero's team's marker for a quest (0 clears it)
    heal, recharge, select_team         the game's actions
    add_status,target,status,seconds,locked|removable,label   puts a standing status on the hero, team or party for the seconds (0: until removed); the label if it cannot
    has_status,target,status,has|lacks,label   the label unless the target has (or lacks) the status
    open_party,on|off,label             the leader opens or closes the party to requests (the label if not the leader)
    add_team, find_party, party_requests  the guild's activities (guild.service): add one of your teams, ask an open party, answer asks
    resurrect,price,label  cure,price,label  uncurse,price,label   the game's paid actions (cancelling jumps to the label)
    pause,ms             wait this long (the browser does)
    sound,clip  music,clip  mute        sound cues (the browser plays them)
    ack                  wait until the player says to go on
    end                  leave the dialog here (the same as jumping to the end label)

The scope of have_item, quests and quest_marker is ``any`` (it holds for any team of the party; for have_item, any hero of it),
``lead`` (for the party's leading team) or ``each`` (for every team of the party; for have_item, a hero of every team). The
level of ``quests`` is a whole number, or ``any`` to count every level. A comparison ``op`` is ``eq``, ``ne``, ``lt``,
``le``, ``gt`` or ``ge``. A party is the one the team acts in right now (``towns.service.acting_party``).

There is always an implicit ``end`` label that leaves the dialog, and running past the last text leaves it too. A label that
is neither defined nor ``end`` is refused when the text is checked (DragonStar would silently leave). The tags the engine
does not act on itself (vend, hawk, shop, inn's Yes, services, heal, recharge, select_team, add_team, find_party, party_requests, resurrect, cure, uncurse) are
handed to the game's ``Npcs.tag``; one it does not handle is passed to the browser as an *activity* to run, which it
finishes by calling ``next`` again.
"""

from dataclasses import dataclass

SCOPES = ("any", "lead", "each")
OPS = ("eq", "ne", "lt", "le", "gt", "ge")
#: The tags that read or change the game's state, which the engine itself runs (through ``advance``'s $state).
STATE_TAGS = ("have_item", "add_item", "remove_item", "quests", "quest_marker", "set_quest_marker", "open_party", "add_status", "has_status")
MAX_TEXT = 20000
MAX_STEPS = 10000  # tags run in one call: a text that jumps in a circle with nothing to show or ask is cut off


class ScriptError(ValueError):
    """The text is not valid dialog, or a call does not fit where the dialog stands."""


def _whole(command: str, value: str, what: str) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > 9:
        raise ScriptError(f"{command} tag: {what} must be a whole number of at most 9 digits, got {value!r}")
    return int(value)


def _name(command: str, value: str, what: str) -> None:
    if not value or len(value) > 64:
        raise ScriptError(f"{command} tag needs {what} (1 to 64 characters)")


def _one_of(command: str, value: str, allowed: tuple[str, ...], what: str) -> None:
    if value not in allowed:
        raise ScriptError(f"{command} tag: {what} is one of {', '.join(allowed)}, got {value!r}")


def _pairs(parts: list[str]) -> list[str]:
    """The label parameters of prompt/label pairs (an odd trailing parameter is a label too)."""
    return parts[1::2] + ([parts[-1]] if len(parts) % 2 == 1 else [])


def check_tag(parts: list[str]) -> list[str]:
    """Checks one tag's parts (the command first) and returns the labels it can send the text to."""
    command, args = parts[0], parts[1:]

    def count(n: int) -> None:
        if len(args) != n:
            raise ScriptError(f"{command} tag takes {n} parameter{'s' if n != 1 else ''}, {len(args)} given")

    if command == "":
        count(0)
    elif command in ("label", "sound", "music", "jump"):
        count(1)
        if not args[0] and command != "jump":
            raise ScriptError(f"{command} tag needs a name")
        return [args[0]] if command == "jump" else []
    elif command in ("team", "party"):
        count(2)
        _whole(command, args[0], "the id")
        return [args[1]]
    elif command == "question":
        if len(args) < 2:
            raise ScriptError(f"question tag needs at least one prompt and label, {len(args)} parameter(s) given")
        return _pairs(args)
    elif command == "switch":
        if len(args) < 2 or len(args) % 2:
            raise ScriptError(f"switch tag needs complete prompt and label pairs, {len(args)} parameter(s) given")
        return _pairs(args)
    elif command == "vend":
        if len(args) < 2:
            raise ScriptError(f"vend tag needs at least one item and price, {len(args)} parameter(s) given")
        for price in args[1:len(args) - len(args) % 2:2]:
            _whole(command, price, "a price")
        return [args[-1]] if len(args) % 2 else []
    elif command == "hawk":
        if not args:
            raise ScriptError("hawk tag needs a margin (a whole percent)")
        _whole(command, args[0], "the margin")
        for price in args[2:len(args) - (len(args) - 1) % 2:2]:
            _whole(command, price, "a price")
        return [args[-1]] if (len(args) - 1) % 2 else []
    elif command == "shop":
        if not 1 <= len(args) <= 2:
            raise ScriptError(f"shop tag takes a shop key and, optionally, a cancel label, {len(args)} parameter(s) given")
        _name(command, args[0], "a shop key")
        return args[1:]
    elif command == "inn":
        count(2)
        _whole(command, args[0], "the price")
        return [args[1]]
    elif command == "services":
        if len(args) < 3 or len(args) % 3:
            raise ScriptError(f"services tag needs complete prompt, price and label triplets, {len(args)} parameter(s) given")
        for price in args[1::3]:
            _whole(command, price, "a price")
        return args[2::3]
    elif command in ("mute", "ack", "end", "heal", "recharge", "select_team", "add_team", "find_party", "party_requests"):
        count(0)
    elif command == "add_status":
        count(5)
        _one_of(command, args[0], ("hero", "team", "party"), "the target")
        _name(command, args[1], "a status")
        _whole(command, args[2], "the seconds (0: until removed)")
        _one_of(command, args[3], ("locked", "removable"), "whether buff-cancelling can remove it")
        return [args[4]]
    elif command == "has_status":
        count(4)
        _one_of(command, args[0], ("hero", "team", "party"), "the target")
        _name(command, args[1], "a status")
        _one_of(command, args[2], ("has", "lacks"), "the test")
        return [args[3]]
    elif command == "open_party":
        count(2)
        _one_of(command, args[0], ("on", "off"), "the setting")
        return [args[1]]
    elif command in ("resurrect", "cure", "uncurse"):
        count(2)
        _whole(command, args[0], "the price")
        return [args[1]]
    elif command in ("have_item", "add_item", "remove_item"):
        count(4 if command == "have_item" else 3)
        _name(command, args[0], "an item")
        if _whole(command, args[1], "the quantity") < 1:
            raise ScriptError(f"{command} tag: the quantity is at least 1")
        if command == "have_item":
            _one_of(command, args[2], SCOPES, "a scope")
        return [args[-1]]
    elif command == "quests":
        count(6)
        _name(command, args[0], "a category")
        if args[1] != "any":
            _whole(command, args[1], "the level (or any)")
        _one_of(command, args[2], OPS, "a comparison")
        _whole(command, args[3], "the number")
        _one_of(command, args[4], SCOPES, "a scope")
        return [args[5]]
    elif command == "quest_marker":
        count(5)
        _name(command, args[0], "a quest")
        _one_of(command, args[1], OPS, "a comparison")
        _whole(command, args[2], "the value")
        _one_of(command, args[3], SCOPES, "a scope")
        return [args[4]]
    elif command == "set_quest_marker":
        count(2)
        _name(command, args[0], "a quest")
        if _whole(command, args[1], "the value") > 1_000_000:
            raise ScriptError("set_quest_marker tag: a marker is at most 1000000")
    elif command == "pause":
        count(1)
        _whole(command, args[0], "the wait in milliseconds")
    else:
        raise ScriptError(f"unknown tag {command!r}")
    return []


class BadAnswer(ScriptError):
    """The answer does not fit what was asked: the dialog stays where it was."""


@dataclass(frozen=True)
class Script:
    text: str
    labels: dict[str, int]  # label -> the position just after its tag


def parse(text: str) -> Script:
    """Checks the dialog text (raising ScriptError that says where) and indexes its labels."""
    if len(text) > MAX_TEXT:
        raise ScriptError(f"a dialog is at most {MAX_TEXT} characters")
    labels: dict[str, int] = {}
    wanted: list[tuple[int, str]] = []
    pos = 0
    while True:
        start = text.find("`", pos)
        if start == -1:
            break
        end = text.find("`", start + 1)
        if end == -1:
            raise ScriptError(f"unpaired backtick at position {start}")
        parts = text[start + 1:end].split(",")
        try:
            wanted += [(start, label) for label in check_tag(parts)]
        except ScriptError as error:
            raise ScriptError(f"at position {start}: {error}") from error
        if parts[0] == "label":
            if parts[1] in labels:
                raise ScriptError(f"at position {start}: label {parts[1]!r} is defined twice")
            labels[parts[1]] = end + 1
        pos = end + 1
    for start, label in wanted:
        if label not in labels and label not in ("", "end"):
            raise ScriptError(f"at position {start}: there is no label {label!r}")
    return Script(text, labels)


@dataclass(frozen=True)
class Who:
    """Who is talking, as the team and party tags see them: every team of the player's, and the hero's party."""

    team_ids: frozenset[int] = frozenset()
    party_id: int | None = None


def _choice(kind: str, args: list[str]) -> dict:
    prompts = [{"text": args[i], "label": args[i + 1]} for i in range(0, len(args) - 1, 2)]
    return {"type": "choice", "kind": kind, "options": prompts, "cancel": args[-1] if kind == "question" and len(args) % 2 else None}


def _activity(command: str, args: list[str]) -> dict:
    """The tag as an activity the browser runs: ``cancel`` is where the text goes when it comes back without a result."""
    cancel = None
    if command == "vend":
        cancel = args[-1] if len(args) % 2 else None
    elif command == "hawk":
        cancel = args[-1] if (len(args) - 1) % 2 else None
    elif command in ("resurrect", "cure", "uncurse"):
        cancel = args[1]
    elif command == "shop":
        cancel = args[1] if len(args) == 2 else None
    return {"type": "activity", "command": command, "parts": args, "cancel": cancel}


async def advance(script: Script, pos: int, prompt: dict | None, choice: int | None, who: Who, tag, state=None) -> dict:
    """Runs the dialog on from $pos, which is where the last call stopped, answering the $prompt it stopped at with $choice
    (the index of an option, or None for Next or cancelling). $tag is the game's ``Npcs.tag`` (command, parts) -> a label,
    "" to go on, or None for not handled. $state is the engine's own (command, parts) -> label for the tags that read or change the
    game's state (``STATE_TAGS``): the label to go to when a test fails or a change cannot be made, "" to go on. Returns ``{"events", "prompt", "pos"}``: what happened (text to show and cues to
    play, in order), and the prompt the dialog stopped at (None when it ended). ``pos`` is where to resume."""
    text, events = script.text, []

    def go(label: str) -> int:
        return script.labels.get(label, len(text)) if label else pos

    pending = None  # a prompt that waits for the end of its text block
    if prompt is not None:
        kind = prompt["type"]
        if kind == "ack":
            if choice is not None:
                raise BadAnswer("there is nothing to choose, only Next")
        elif kind == "choice":
            if choice is None:
                label = prompt["cancel"]
            elif 0 <= choice < len(prompt["options"]):
                label = prompt["options"][choice]["label"]
            else:
                raise BadAnswer("that is not one of the choices")
            if prompt["kind"] == "inn" and label == "":  # Yes: the game rests the party
                handled = await tag("inn", prompt["parts"])
                if handled is None:
                    return {"events": events, "prompt": {"type": "activity", "command": "inn", "parts": prompt["parts"], "cancel": None}, "pos": pos}
                label = handled
            pos = go(label) if label else pos
        else:  # an activity the browser ran
            if choice is not None:
                raise BadAnswer("an activity is finished with Next, there is nothing to choose")
            pos = go(prompt["cancel"]) if prompt["cancel"] else pos
    steps = 0
    while pos < len(text):
        steps += 1
        if steps > MAX_STEPS:
            raise ScriptError("the dialog goes in circles")
        start = text.find("`", pos)
        end = start if start > -1 else len(text)
        if pending is not None:
            newline = text.find("\n", pos)
            if newline > -1 and newline < end:
                end = newline + 1
        if end > pos:
            _show(events, text[pos:end])
            pos = end
        if pending is not None:
            return await _ask(pending, events, pos, tag, script, who, state)
        if start == -1:
            break
        end_tag = text.find("`", start + 1)
        parts = text[start + 1:end_tag].split(",")
        command, args = parts[0], parts[1:]
        pos = end_tag + 1
        if command == "":
            _show(events, "`")
        elif command == "label":
            pass
        elif command == "jump":
            pos = go(args[0]) if args[0] else pos
        elif command == "team":
            if int(args[0]) not in who.team_ids:
                pos = go(args[1])
        elif command == "party":
            if who.party_id != int(args[0]):
                pos = go(args[1])
        elif command in ("question", "switch"):
            pending = _choice(command, args)
        elif command == "inn":
            pending = {"type": "choice", "kind": "inn", "options": [{"text": "Yes", "label": ""}, {"text": "No", "label": args[1]}],
                       "cancel": args[1], "parts": args}
        elif command in ("vend", "hawk", "shop"):
            pending = _activity(command, args)
        elif command == "end":
            return {"events": events, "prompt": None, "pos": len(text)}
        elif command in STATE_TAGS:
            if state is None:
                raise ScriptError(f"this dialog reads the game's state ({command}), and there is no one to ask")
            failed = await state(command, args)
            if failed:
                pos = go(failed)
        elif command == "ack":
            return {"events": events, "prompt": {"type": "ack"}, "pos": pos}
        elif command == "pause":
            events.append({"type": "pause", "ms": int(args[0])})
        elif command in ("sound", "music"):
            events.append({"type": command, "clip": args[0]})
        elif command == "mute":
            events.append({"type": "mute"})
        else:  # the game's: heal, recharge, select_team, services, resurrect, cure, uncurse
            handled = await tag(command, args)
            if handled is None:
                return {"events": events, "prompt": _activity(command, args), "pos": pos}
            if handled:
                pos = go(handled)
    if pending is not None:
        return await _ask(pending, events, pos, tag, script, who, state)
    return {"events": events, "prompt": None, "pos": len(text)}


async def _ask(pending: dict, events: list, pos: int, tag, script: Script, who: Who, state) -> dict:
    """A vend or hawk goes to the game first (it may deal with it); everything else is shown as the prompt."""
    if pending["type"] == "activity":
        handled = await tag(pending["command"], pending["parts"])
        if handled is not None:  # it was dealt with: go where it says, or on with the text for ""
            rest = await advance(script, script.labels.get(handled, len(script.text)) if handled else pos, None, None, who, tag, state)
            return {**rest, "events": events + rest["events"]}
    return {"events": events, "prompt": pending, "pos": pos}


def _show(events: list, text: str) -> None:
    if events and events[-1]["type"] == "text":
        events[-1]["text"] += text
    else:
        events.append({"type": "text", "text": text})

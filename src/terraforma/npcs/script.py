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
    inn,price,label      after the text that follows: Yes or No; No (or cancelling) jumps to the label, Yes lets the game rest the party
    services,prompt1,price1,label1,...  a menu of paid services (the game answers it)
    heal, recharge, select_team         the game's actions
    resurrect,price,label  cure,price,label  uncurse,price,label   the game's paid actions (cancelling jumps to the label)
    pause,ms             wait this long (the browser does)
    sound,clip  music,clip  mute        sound cues (the browser plays them)
    ack                  wait until the player says to go on
    end                  leave the dialog here (the same as jumping to the end label)

There is always an implicit ``end`` label that leaves the dialog, and running past the last text leaves it too. A label that
is neither defined nor ``end`` is refused when the text is checked (DragonStar would silently leave). The tags the engine
does not act on itself (vend, hawk, inn's Yes, services, heal, recharge, select_team, resurrect, cure, uncurse) are
handed to the game's ``Npcs.tag``; one it does not handle is passed to the browser as an *activity* to run, which it
finishes by calling ``next`` again.
"""

from dataclasses import dataclass

MAX_TEXT = 20000
MAX_STEPS = 10000  # tags run in one call: a text that jumps in a circle with nothing to show or ask is cut off


class ScriptError(ValueError):
    """The text is not valid dialog, or a call does not fit where the dialog stands."""


def _whole(command: str, value: str, what: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ScriptError(f"{command} tag: {what} must be a whole number, got {value!r}")
    return int(value)


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
    elif command in ("mute", "ack", "end", "heal", "recharge", "select_team"):
        count(0)
    elif command in ("resurrect", "cure", "uncurse"):
        count(2)
        _whole(command, args[0], "the price")
        return [args[1]]
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
    return {"type": "activity", "command": command, "parts": args, "cancel": cancel}


async def advance(script: Script, pos: int, prompt: dict | None, choice: int | None, who: Who, tag) -> dict:
    """Runs the dialog on from $pos, which is where the last call stopped, answering the $prompt it stopped at with $choice
    (the index of an option, or None for Next or cancelling). $tag is the game's ``Npcs.tag`` (command, parts) -> a label,
    "" to go on, or None for not handled. Returns ``{"events", "prompt", "pos"}``: what happened (text to show and cues to
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
            return await _ask(pending, events, pos, tag, script, who)
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
        elif command in ("vend", "hawk"):
            pending = _activity(command, args)
        elif command == "end":
            return {"events": events, "prompt": None, "pos": len(text)}
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
        return await _ask(pending, events, pos, tag, script, who)
    return {"events": events, "prompt": None, "pos": len(text)}


async def _ask(pending: dict, events: list, pos: int, tag, script: Script, who: Who) -> dict:
    """A vend or hawk goes to the game first (it may deal with it); everything else is shown as the prompt."""
    if pending["type"] == "activity":
        handled = await tag(pending["command"], pending["parts"])
        if handled is not None:  # it was dealt with: go where it says, or on with the text for ""
            rest = await advance(script, script.labels.get(handled, len(script.text)) if handled else pos, None, None, who, tag)
            return {**rest, "events": events + rest["events"]}
    return {"events": events, "prompt": pending, "pos": pos}


def _show(events: list, text: str) -> None:
    if events and events[-1]["type"] == "text":
        events[-1]["text"] += text
    else:
        events.append({"type": "text", "text": text})

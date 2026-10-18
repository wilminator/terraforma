// The fight as the page knows it: who stands, with how much, and what each event of a round does to them. The server's event log is
// the truth (the same events replay a fight on the server); this model only follows it so the screen can show each step as it is played.
// It does no drawing and asks for nothing: an event goes in, and what to say and what to mark on the fighters comes out.

/** The key of a fighter's address: "party.group.character". */
export function addressKey(party, group, character) {
  return `${party}.${group}.${character}`;
}

const keyOf = (data) => addressKey(data[0], data[1], data[2]);

export class FightModel {
  /** $view is what GET /api/fights/{id} says. */
  constructor(view) {
    this.id = view.id;
    this.round = view.round;
    this.over = view.over;
    this.vital = view.vital ?? "HP";
    this.deadline = view.deadline;
    this.seat = view.seat ?? null; // only for the player who commands a party's monsters: { party, absent, admin }
    this.actor = null; // whose turn the log is on
    this.fighters = new Map(
      view.fighters.map((each) => [
        addressKey(each.party, each.group, each.character),
        { ...each, resources: Object.fromEntries(Object.entries(each.resources).map(([name, pair]) => [name, [...pair]])), statuses: [...each.statuses] },
      ]),
    );
  }

  fighter(key) {
    return this.fighters.get(key) ?? null;
  }

  /** What the log calls a skill, spell or item: its name when the acting hero is the player's (whose menu lists them), else its key. */
  #called(type, key) {
    const menu = this.fighter(this.actor)?.menu;
    const found = type === "Item" ? menu?.items.find((each) => each.key === key) : menu?.abilities.find((each) => each.key === key);
    return found?.name ?? key;
  }

  /** The keys of the fighters a command from $actor can be aimed at: "enemy" (the standing fighters of other parties), "ally" (the standing
   * fighters of its own, itself included), "downed" (its fallen allies, for a revive) or "none" (the command names its own targets). */
  targets(actor, aim) {
    const party = this.fighter(actor)?.party;
    const wanted = {
      enemy: (each) => each.party !== party && each.alive,
      ally: (each) => each.party === party && each.alive,
      downed: (each) => each.party === party && !each.alive,
    }[aim];
    if (!wanted) return [];
    return [...this.fighters].filter(([, each]) => !each.fled && wanted(each)).map(([key]) => key);
  }

  /** Takes from a fresh $view what only the server knows (what each of the player's heroes can do, who has chosen), and leaves the numbers
   * the events have brought up to date. */
  refresh(view) {
    this.seat = view.seat ?? null;
    for (const each of view.fighters) {
      const mine = this.fighter(addressKey(each.party, each.group, each.character));
      if (!mine) continue;
      mine.menu = each.menu;
      mine.committed = each.committed;
    }
  }

  /** The vital resource as [current, maximum]. */
  vitalOf(fighter) {
    return fighter.resources[this.vital] ?? [0, 0];
  }

  /**
   * Follows one event of the log. Returns { text: [key, values] | null, marks: [{ key, kind, amount? }], reload }:
   * what to say (a text key and its values), what to show on which fighters, and whether the fight changed in a way only the server's
   * view can say (a party joined).
   */
  apply([type, data]) {
    const name = (key) => this.fighter(key)?.name ?? "?";
    const done = (text = null, marks = [], reload = false) => ({ text, marks, reload });
    const fighter = () => this.fighter(keyOf(data));
    switch (type) {
      case "Turn": {
        this.actor = keyOf(data);
        return done(["fight.turn", { name: name(this.actor) }], [{ key: this.actor, kind: "turn" }]);
      }
      case "Attack":
        return done(["fight.attack", { name: name(this.actor) }], [{ key: this.actor, kind: "act", cue: "attack_close" }]);
      case "Skill":
      case "Spell":
      case "Item":
        return done([`fight.${type.toLowerCase()}`, { name: name(this.actor), what: this.#called(type, data[0]) }], [{ key: this.actor, kind: "act", cue: type.toLowerCase() }]);
      case "Defend": {
        // (told at the start of the round, for the fighter whose address it carries: a defender takes no turn)
        const who = data.length >= 3 ? keyOf(data) : this.actor;
        return done(["fight.defend", { name: name(who) }], [{ key: who, kind: "defend" }]);
      }
      case "Run":
        return done(["fight.run", { name: name(this.actor) }]);
      case "Fled": {
        const who = fighter();
        if (who) who.fled = true;
        return done(["fight.fled", { name: name(keyOf(data)) }], [{ key: keyOf(data), kind: "fled" }]);
      }
      case "FleeFailed":
        return done(["fight.flee_failed", { name: name(keyOf(data)) }]);
      case "Miss":
        return done(["fight.miss", { name: name(keyOf(data)) }], [{ key: keyOf(data), kind: "miss" }]);
      case "NoEffect":
        return done(["fight.no_effect", { name: name(keyOf(data)) }]);
      case "NoMP":
        return done(["fight.no_mp", { name: name(keyOf(data)) }]);
      case "NoAmmo":
        return done(["fight.no_ammo", { name: name(keyOf(data)) }]);
      case "Damage": {
        const who = fighter();
        if (who) this.#change(who, this.vital, -data[3]);
        return done([data[4] ? "fight.critical" : "fight.damage", { name: name(keyOf(data)), amount: data[3] }], [{ key: keyOf(data), kind: "damage", amount: data[3] }]);
      }
      case "Restore": {
        const who = fighter();
        if (who) this.#change(who, data[3], data[4]);
        return done(["fight.restore", { name: name(keyOf(data)), amount: data[4], resource: data[3] }], [{ key: keyOf(data), kind: "heal", amount: data[4] }]);
      }
      case "AlterStat": {
        const who = fighter();
        if (who) this.#change(who, data[3], data[4]);
        return done(null, [{ key: keyOf(data), kind: "alter" }]);
      }
      case "Died": {
        const who = fighter();
        if (who) {
          who.alive = false;
          if (who.resources[this.vital]) who.resources[this.vital][0] = 0;
        }
        return done(["fight.died", { name: name(keyOf(data)) }], [{ key: keyOf(data), kind: "died" }]);
      }
      case "Revived": {
        const who = fighter();
        if (who) who.alive = true;
        return done(["fight.revived", { name: name(keyOf(data)) }], [{ key: keyOf(data), kind: "revived" }]);
      }
      case "StatusApplied": {
        const who = fighter();
        if (who && !who.statuses.includes(data[3])) who.statuses.push(data[3]);
        return done(["fight.status", { name: name(keyOf(data)), status: data[3] }], [{ key: keyOf(data), kind: "status" }]);
      }
      case "StatusRemoved": {
        const who = fighter();
        if (who) who.statuses = who.statuses.filter((each) => each !== data[3]);
        return done(["fight.status_gone", { name: name(keyOf(data)), status: data[3] }], [{ key: keyOf(data), kind: "status" }]);
      }
      case "TurnSkipped":
        return done(["fight.skipped", { name: name(keyOf(data)) }]);
      case "XpEarned":
        return done(["fight.xp", { name: name(keyOf(data)), amount: data[3] }]);
      case "LevelUp": {
        // (the stats a level adds are the resources' maximums: the fight is paid out and the hero stands there at once)
        const who = fighter();
        for (const [stat, gain] of Object.entries(data[4] ?? {})) {
          if (who?.resources[stat]) who.resources[stat][1] += gain;
        }
        return done(["fight.level", { name: name(keyOf(data)), level: data[3] }], [{ key: keyOf(data), kind: "level" }]);
      }
      case "Gold":
        return done(["fight.gold", { amount: data[2] }]);
      case "Drop":
        return done(["fight.drop", { name: name(keyOf(data)), item: data[3], quantity: data[4] }]);
      case "DropHeld":
        return done(["fight.drop_held", { item: data[1], quantity: data[2] }]);
      case "PartyLost":
        return done(["fight.party_lost", { party: data[0] }]);
      case "PartyJoined":
        return done(["fight.party_joined", { party: data[0] }], [], true);
      case "FightOver":
        this.over = true;
        return done(["fight.over", {}]);
      default:
        return done(); // (an event the screen has nothing to show for: tokens ticking, inventory moves, the relation prompts, ...)
    }
  }

  /** The numbers of one round's end: the round the fight now waits for. */
  endRound(round, over, deadline) {
    this.round = round + 1;
    this.over = this.over || over;
    this.deadline = deadline;
    this.actor = null;
    for (const each of this.fighters.values()) each.committed = false;
  }

  #change(fighter, resource, amount) {
    const pair = fighter.resources[resource];
    if (pair) pair[0] = Math.min(Math.max(pair[0] + amount, 0), pair[1]);
  }
}

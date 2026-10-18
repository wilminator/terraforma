// What the dialog panel shows when an NPC's dialog reaches an *activity*: a tag the engine leaves to the browser (the shop's `vend`,
// `hawk` and `shop`, and any a game's `Npcs.tag` does not handle). The engine adds the shop; a game adds its own in its module:
//
//   shell.activities.add("forge", ({ shell, heroId, prompt, finish, problem }) => h("div", {}, ...));
//
// $build gets { shell, roster, heroId, prompt (command and parts), finish (async: goes on with the dialog, as Next does),
// proceed (takes the frame a call of the activity answered with, when the server already went on), problem (an element to show a
// refusal in) } and returns the element to show. The conversation goes on only when it calls finish() or proceed(frame).

import { guildActivity } from "./guild.js";
import { shopActivity } from "./shop.js";

export class Activities {
  #builds = new Map();

  add(command, build) {
    if (this.#builds.has(command)) throw new Error(`There is already a screen for "${command}".`);
    this.#builds.set(command, build);
  }

  get(command) {
    return this.#builds.get(command) ?? null;
  }

  /** The engine's own: the three tags that open a shop, and the guild's three. */
  addEngine() {
    for (const command of ["vend", "hawk", "shop"]) this.add(command, shopActivity);
    for (const command of ["add_team", "find_party", "party_requests"]) this.add(command, guildActivity);
  }
}

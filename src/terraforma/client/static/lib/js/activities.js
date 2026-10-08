// What the dialog panel shows when an NPC's dialog reaches an *activity*: a tag the engine leaves to the browser (the shop's `vend`,
// `hawk` and `shop`, and any a game's `Npcs.tag` does not handle). The engine adds the shop; a game adds its own in its module:
//
//   shell.activities.add("forge", ({ shell, heroId, prompt, finish, problem }) => h("div", {}, ...));
//
// $build gets { shell, roster, heroId, prompt (command and parts), finish (async: goes on with the dialog, as Next does),
// problem (an element to show a refusal in) } and returns the element to show. The conversation goes on only when it calls finish().

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

  /** The engine's own: the three tags that open a shop. */
  addEngine() {
    for (const command of ["vend", "hawk", "shop"]) this.add(command, shopActivity);
  }
}

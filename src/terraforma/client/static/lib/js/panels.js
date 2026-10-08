// The panels of the game client: a row of buttons in the bar, and the panel they open over the stage's right side.
// The engine adds its own (teams, party, nearby, talk); a game adds more with shell.panels.add(id, build) in its module:
//
//   shell.text.set({ "panel.inn": "Inn" });
//   shell.panels.add("inn", (shell, roster) => h("div", { class: "panel" }, h("h2", {}, shell.text.get("panel.inn")), ...));
//
// $build is called once the player is logged in; it returns an element, or { element, show } where show() runs each time
// the panel is opened. The panel's button says shell.text.get("panel.<id>").

import { dialogPanel } from "./dialog.js";
import { h, replace } from "./dom.js";
import { nearbyPanel } from "./nearby.js";
import { partyPanel } from "./party.js";
import { teamsPanel } from "./teams.js";

export class Panels {
  entries = [];
  #opener = null;

  add(id, build) {
    if (this.entries.some((entry) => entry.id === id)) throw new Error(`There is already a panel called "${id}".`);
    this.entries.push({ id, build });
  }

  /** Puts the engine's panels first, so a game's come after them. */
  addEngine() {
    this.add("teams", teamsPanel);
    this.add("party", partyPanel);
    this.add("nearby", nearbyPanel);
    this.add("dialog", dialogPanel);
  }

  /** Opens the panel (leaving it open if it already is): the nearby list opens the talk panel when a conversation starts. */
  open(id) {
    this.#opener?.(id);
  }

  setOpener(opener) {
    this.#opener = opener;
  }
}

/** Builds every panel for a logged-in player: { buttons, drawer, open(id) }. $first is the id of the panel to show at the start. */
export function mountPanels(shell, roster, first) {
  const t = (key) => shell.text.get(key);
  const drawer = h("aside", { class: "drawer", id: "drawer", hidden: true, "aria-label": t("play.menu") });
  const shown = new Map();
  let open = null;

  for (const { id, build } of shell.panels.entries) {
    const made = build(shell, roster);
    const built = made instanceof Element ? { element: made } : made;
    built.element.hidden = true;
    shown.set(id, built);
  }
  drawer.append(...[...shown.values()].map((built) => built.element));

  const buttons = h("nav", { class: "tabs", "aria-label": t("play.menu") });

  function draw() {
    for (const [id, built] of shown) built.element.hidden = id !== open;
    drawer.hidden = open === null;
    for (const button of buttons.children) button.setAttribute("aria-pressed", String(button.dataset.panel === open));
  }

  function toggle(id) {
    open = open === id ? null : id;
    draw();
    if (open) shown.get(open).show?.();
  }

  shell.panels.setOpener((id) => {
    if (shown.has(id) && open !== id) toggle(id);
  });

  replace(
    buttons,
    [...shown.keys()].map((id) => h("button", { type: "button", "data-panel": id, "aria-pressed": "false", onclick: () => toggle(id) }, t(`panel.${id}`))),
  );
  drawer.prepend(h("button", { type: "button", class: "close", "aria-label": t("panel.close"), onclick: () => { open = null; draw(); } }, "×"));
  if (first && shown.has(first)) toggle(first);
  return { buttons, drawer };
}

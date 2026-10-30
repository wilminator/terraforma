// The admin page: where an admin writes events (manual quests) and monsters and ends a slot's quest. One login (the account page) serves it. Nothing here
// decides who is an admin: the first admin call answers 403 to anyone else, and the page then says so. Every call is checked again by the server.

import * as api from "./api.js";
import { eventsSection } from "./admin-events.js";
import { monstersSection } from "./admin-monsters.js";
import { proposalsSection } from "./admin-proposals.js";
import { savedMapsSection } from "./admin-saved-maps.js";
import { worldSection } from "./admin-world.js";
import { slotsSection } from "./admin-slots.js";
import { stateSection } from "./admin-state.js";
import { h, replace } from "./dom.js";
import { Shell } from "./shell.js";

const shell = new Shell("admin");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

function show(sections) {
  const tabs = h("div", { class: "tabs", role: "group", "aria-label": t("admin.sections") });
  const loaded = new Set();
  const pick = async (name) => {
    for (const [each, section] of Object.entries(sections)) section.element.hidden = each !== name;
    for (const button of tabs.children) button.setAttribute("aria-pressed", String(button.dataset.section === name));
    if (!loaded.has(name)) {
      loaded.add(name);
      await sections[name].load();
    }
  };
  for (const name of Object.keys(sections)) {
    tabs.append(h("button", { type: "button", "data-section": name, "aria-pressed": "false", onclick: () => pick(name) }, t(`admin.section_${name}`)));
  }
  replace(main, h("div", { class: "stack" }, h("h1", {}, t("admin.title")), tabs, ...Object.values(sections).map((section) => section.element)));
  return pick(Object.keys(sections)[0]);
}

await shell.start();
document.title = `${t("admin.title")} · ${shell.game.game ?? "TerraForma"}`;
if (!(await shell.restore())) {
  location.replace("/account/?next=admin"); // one login; it sends the admin back here
} else {
  try {
    await api.get("/api/admin/monsters?limit=1"); // (any admin read will do: it answers 403 to everyone else)
    await show({ events: eventsSection(shell), monsters: monstersSection(shell), slots: slotsSection(shell), proposals: proposalsSection(shell), world: worldSection(shell), maps: savedMapsSection(shell), state: stateSection(shell) });
  } catch (error) {
    if (!(error instanceof api.ApiError)) throw error;
    replace(main, h("div", { class: "stack" }, h("h1", {}, t("admin.title")), h("p", { class: error.status === 403 ? "muted" : "problem", role: "alert", id: "admin-refused" }, error.status === 403 ? t("admin.not_admin") : error.message)));
  }
  if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

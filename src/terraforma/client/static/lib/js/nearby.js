// The nearby panel: what the chosen hero can reach for an action (talk, invite, open, search, fight, help, and any a game adds)
// from where they stand: NPCs, map objects and other parties, nearest first. The list is good for a few seconds, then asks again.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function nearbyPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const note = h("p", { class: "muted small", role: "status" });
  const list = h("ul", { class: "rows", "aria-label": t("nearby.list") });
  const hero = h("select", { id: "nearby-hero", "aria-label": t("nearby.hero"), onchange: () => roster.selectHero(Number(hero.value)) });
  const action = h("select", { id: "nearby-action", "aria-label": t("nearby.action"), onchange: () => refresh() });
  let stale = null;

  function describe(entry) {
    if (entry.kind === "party") return t("nearby.party", { teams: entry.teams.map((team) => team.name).join(", ") });
    if (entry.kind === "object") return t("nearby.object", { name: entry.name, object: entry.object });
    return entry.name;
  }

  function show(answer) {
    clearTimeout(stale);
    replace(
      list,
      answer.nearby.length
        ? answer.nearby.map((entry) => h("li", { class: "row", "data-kind": entry.kind }, h("span", {}, describe(entry)), h("span", { class: "muted small" }, t("nearby.distance", { steps: entry.distance }))))
        : h("li", { class: "muted" }, t("nearby.nothing")),
    );
    note.textContent = "";
    if (answer.valid_for > 0) {
      stale = setTimeout(() => {
        list.classList.add("stale");
        note.textContent = t("nearby.stale");
      }, answer.valid_for * 1000);
    }
    list.classList.remove("stale");
  }

  async function refresh() {
    if (roster.heroId === null) return;
    await attempt(shell, problem, async () => show(await api.get(`/api/heroes/${roster.heroId}/nearby/${action.value}`)));
  }

  function draw() {
    replace(hero, roster.heroes.map((each) => h("option", { value: each.id, selected: each.id === roster.heroId }, each.name)));
    if (roster.heroId === null) {
      replace(list, h("li", { class: "muted" }, t("nearby.no_hero")));
      note.textContent = "";
    }
  }

  let shownFor = null;
  roster.onChange(() => {
    draw();
    if (roster.heroId !== shownFor) {
      shownFor = roster.heroId;
      refresh();
    }
  });
  replace(action, shell.actions.map((each) => h("option", { value: each }, t(`action.${each}`))));
  draw();

  const element = h(
    "div",
    { class: "panel", id: "panel-nearby" },
    h("h2", {}, t("panel.nearby")),
    h("div", { class: "field" }, h("label", { for: "nearby-hero" }, t("nearby.hero")), hero),
    h("div", { class: "field" }, h("label", { for: "nearby-action" }, t("nearby.action")), action),
    list,
    problem,
    note,
    h("button", { type: "button", onclick: refresh }, t("panel.refresh")),
  );
  return { element, show: refresh };
}

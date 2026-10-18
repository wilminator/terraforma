// The Drops panel: loot a fight held for the party to settle instead of handing it out (the game's `Rules.drop_mode`). A need/want drop waits
// for every hero of the party that fought to say Need, Want or Pass (a hero may change their mind until the last one answers); then the server
// rolls and says who won. A hand-out is given by one hero to another when the game's rules let them. Everything is decided by the server, which
// says no in its own words; the others' answers stay hidden until the drop is settled, and what a settled drop came to is shown here until the panel is
// left, so the player sees who won and what rolled.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

const CHOICES = ["need", "want", "pass"];

export function dropsPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "drops-problem" });
  const body = h("div", { id: "drops-body" });
  const root = h("div", { class: "panel drops", id: "panel-drops" }, h("h2", {}, t("panel.drops")), body);
  let waiting = []; // GET /api/heroes/{id}/pending-drops
  let settled = []; // what drops came to since the panel opened, newest first
  let loaded = null; // the hero the view is of
  const gives = new Map(); // drop id -> the hero chosen to hand it to

  async function load() {
    const id = roster.heroId;
    loaded = id;
    waiting = id === null ? [] : await api.get(`/api/heroes/${id}/pending-drops`);
    draw();
  }

  function refresh() {
    attempt(shell, problem, load);
  }

  /** A call on one drop: if it settled the drop, the result is kept; the list is read again either way. */
  function settle(drop, path, values) {
    attempt(shell, problem, async () => {
      const answer = await api.post(`/api/heroes/${loaded}/pending-drops/${drop.id}/${path}`, values);
      if (answer.status !== "open") settled = [answer, ...settled];
      await load();
    });
  }

  const what = (drop) => t("drops.what", { count: drop.qty, item: drop.name });

  function choiceButtons(drop) {
    return h(
      "div",
      { class: "inline", role: "group", "aria-label": t("drops.choices", { item: drop.name }) },
      CHOICES.map((choice) =>
        h("button", { type: "button", "data-choice": choice, "aria-pressed": String(drop.your_choice === choice), "aria-label": t("drops.choose", { choice: t(`drops.${choice}`), item: drop.name }), onclick: () => settle(drop, "choose", { choice }) }, t(`drops.${choice}`)),
      ),
    );
  }

  function handOut(drop) {
    const to = h("select", { "aria-label": t("drops.give_to", { item: drop.name }), onchange: (event) => gives.set(drop.id, event.target.value) }, drop.heroes.map((hero) => h("option", { value: String(hero.id), selected: String(hero.id) === gives.get(drop.id) }, hero.name)));
    return h("div", { class: "inline" }, to, h("button", { type: "button", "aria-label": t("drops.give", { item: drop.name }), onclick: () => settle(drop, "assign", { to_hero_id: Number(to.value) }) }, t("drops.hand")));
  }

  function openRow(drop) {
    const mine = drop.your_choice ? t("drops.your_answer", { choice: t(`drops.${drop.your_choice}`) }) : t("drops.unanswered");
    return h(
      "li",
      { class: "row", "data-drop": drop.id },
      h("span", {}, h("strong", {}, what(drop)), " ", h("span", { class: "muted small" }, drop.mode === "need_want" ? mine : t("drops.handed_by_leader"))),
      drop.mode === "need_want" ? choiceButtons(drop) : handOut(drop),
    );
  }

  function settledRow(drop) {
    const rolls = drop.rolls.filter((each) => each.roll !== null).map((each) => t("drops.rolled", { hero: each.hero ?? "?", choice: t(`drops.${each.choice}`), roll: each.roll }));
    const result = drop.status === "awarded" ? t("drops.won", { hero: drop.winner ?? "?", what: what(drop) }) : t("drops.unclaimed", { what: what(drop) });
    const lost = drop.lost ? ` ${t("drops.lost", { count: drop.lost })}` : "";
    return h("li", { class: "row", "data-settled": drop.id }, h("span", {}, result, lost, rolls.length ? h("span", { class: "muted small" }, ` ${rolls.join("; ")}`) : null));
  }

  function draw() {
    if (loaded === null) {
      replace(body, h("p", { class: "muted", id: "drops-no-hero" }, t("drops.no_hero")), problem);
      return;
    }
    const heroes = h(
      "select",
      { id: "drops-hero", "aria-label": t("drops.hero"), onchange: (event) => roster.selectHero(Number(event.target.value)) },
      roster.heroes.map((hero) => h("option", { value: String(hero.id), selected: hero.id === loaded }, hero.name)),
    );
    replace(
      body,
      h("div", { class: "inline" }, heroes, h("button", { type: "button", onclick: refresh }, t("panel.refresh"))),
      waiting.length ? h("ul", { class: "rows", id: "drops-waiting", "aria-label": t("drops.waiting") }, waiting.map(openRow)) : h("p", { class: "muted", id: "drops-none" }, t("drops.none")),
      problem,
      settled.length ? h("h3", {}, t("drops.settled")) : null,
      settled.length ? h("ul", { class: "rows", id: "drops-settled", "aria-label": t("drops.settled") }, settled.map(settledRow)) : null,
    );
  }

  roster.onChange(() => {
    if (!root.hidden && roster.heroId !== loaded) refresh();
  });
  draw();
  return { element: root, show: refresh };
}

// The Quests panel: the team's quest of the week and the quests the slots (the guilds' promotions and obligations) hold now. The browser only
// reads them and asks for them (the offer calls); taking a quest and finishing it is the game's (its dungeons and dialog), and a quest's spec is
// the game's own, so only its name, level, status and time limits are shown here.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function questsPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "quests-problem" });
  const note = h("p", { class: "note", role: "status", id: "quests-note" });
  const body = h("div", { id: "quests-body" });
  const root = h("div", { class: "panel quests", id: "panel-quests" }, h("h2", {}, t("panel.quests")), body);
  let weekly = null; // GET /api/teams/{id}/weekly-quest: the quest, or null before the team asked
  let slots = []; // GET /api/teams/{id}/slot-quests
  let loaded = null; // the team the view is of

  /** Seconds since 1970 (or an ISO date) as the player's own clock reads it. */
  const when = (value) => new Date(typeof value === "number" ? value * 1000 : value).toLocaleString();
  const status = (value) => t(`quests.status_${value}`);

  async function load() {
    const id = roster.teamId;
    if (id === null) {
      loaded = null;
      weekly = null;
      slots = [];
      return draw();
    }
    const [week, held] = await Promise.all([api.get(`/api/teams/${id}/weekly-quest`), api.get(`/api/teams/${id}/slot-quests`)]);
    loaded = id;
    weekly = week.quest;
    slots = held.slots;
    draw();
  }

  function refresh() {
    attempt(shell, problem, load);
  }

  /** An offer call; what is shown afterwards is read again, so it is the server's. */
  function ask(path, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      await api.post(`/api/teams/${loaded}/${path}`, {});
      await load();
      note.textContent = done;
    });
  }

  function timing(started, deadline, failure) {
    return [
      started ? h("p", { class: "muted small" }, t("quests.started", { when: when(started) })) : null,
      deadline ? h("p", { class: "muted small" }, t("quests.deadline", { when: when(deadline) })) : null,
      failure ? h("p", { class: "problem small" }, t("quests.failed", { reason: failure })) : null,
    ];
  }

  function weeklyCard() {
    if (weekly === null) {
      return h(
        "div",
        { class: "card", id: "quests-weekly" },
        h("h3", {}, t("quests.weekly")),
        h("p", { class: "muted", id: "quests-weekly-none" }, t("quests.weekly_none")),
        h("button", { type: "button", id: "quests-weekly-ask", onclick: () => ask("weekly-quest/offer", t("quests.asked")) }, t("quests.ask")),
      );
    }
    return h(
      "div",
      { class: "card", id: "quests-weekly", "data-status": weekly.status },
      h("h3", {}, t("quests.weekly")),
      h("p", {}, h("strong", { id: "quests-weekly-name" }, weekly.name), ` ${t("quests.level", { level: weekly.level })}`),
      h("p", { class: "muted small", id: "quests-weekly-status" }, `${status(weekly.status)} · ${t("quests.ends", { when: when(weekly.ends) })}`),
      ...timing(weekly.started, weekly.deadline, weekly.failure),
    );
  }

  function slotRow(slot) {
    const mine = slot.team;
    return h(
      "li",
      { class: "row", "data-slot": slot.key },
      h(
        "div",
        {},
        h("strong", {}, slot.name),
        h("span", { class: "muted small" }, ` ${t("quests.slot_kind", { guild: slot.guild, rank: slot.rank })}${slot.obligation ? ` · ${t("quests.obligation")}` : ""}`),
        h("p", {}, slot.quest.name),
        h("p", { class: "muted small" }, t("quests.rotates", { when: when(slot.quest.ends) })),
        mine ? h("p", { class: "muted small", "data-team-status": mine.status }, status(mine.status)) : null,
        ...(mine ? timing(mine.started, mine.deadline, mine.failure) : []),
      ),
      mine ? null : h("button", { type: "button", "aria-label": t("quests.ask_slot", { name: slot.name }), onclick: () => ask(`slot-quests/${slot.key}/offer`, t("quests.asked_slot", { name: slot.name })) }, t("quests.ask")),
    );
  }

  function draw() {
    if (loaded === null) {
      replace(body, h("p", { class: "muted", id: "quests-none" }, t("quests.no_team")), problem);
      return;
    }
    const teams = h(
      "select",
      { id: "quests-team", "aria-label": t("quests.team"), onchange: (event) => roster.selectTeam(Number(event.target.value)) },
      roster.teams.map((team) => h("option", { value: String(team.id), selected: team.id === loaded }, team.name)),
    );
    replace(
      body,
      h("div", { class: "inline" }, teams, h("button", { type: "button", onclick: refresh }, t("panel.refresh"))),
      weeklyCard(),
      h("h3", {}, t("quests.slots")),
      slots.length ? h("ul", { class: "rows", id: "quests-slots", "aria-label": t("quests.slots") }, slots.map(slotRow)) : h("p", { class: "muted", id: "quests-slots-none" }, t("quests.slots_none")),
      problem,
      note,
    );
  }

  roster.onChange(() => {
    if (!root.hidden && roster.teamId !== loaded) refresh();
  });
  draw();
  return { element: root, show: refresh };
}

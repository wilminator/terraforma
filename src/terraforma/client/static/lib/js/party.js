// The party panel: a team enters the game as a party, and in a town it says when it is ready to leave, comes back or leaves the party.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function partyPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const note = h("p", { class: "note", role: "status" });
  const status = h("div", { class: "party-status", "aria-live": "polite" });
  const pick = h("select", { id: "party-team", "aria-label": t("party.team"), onchange: () => roster.selectTeam(Number(pick.value)) });
  let town = null; // what the server last said about the chosen team's town visit
  let shown = null; // the team that answer is about

  const teamName = (id) => roster.team(id)?.name ?? t("party.other_team");

  async function refresh() {
    const team = roster.team(roster.teamId);
    shown = team?.id ?? null;
    town = null;
    if (team) await attempt(shell, problem, async () => { town = await api.get(`/api/teams/${team.id}/town`); });
    draw();
  }

  async function act(path, done) {
    const team = roster.team(roster.teamId);
    note.textContent = "";
    await attempt(shell, problem, async () => {
      const answer = await api.post(`/api/teams/${team.id}/${path}`);
      if (path === "play") {
        note.textContent = t("party.playing", { team: team.name, party: answer.party, map: answer.map_id, x: answer.x, y: answer.y });
      } else if (path === "town/ready") {
        town = answer;
        note.textContent = answer.fight ? t("party.fight_started") : t(answer.in_town ? "party.waiting" : "party.left");
      } else {
        town = answer;
        note.textContent = t(done);
      }
    });
    if (problem.textContent) draw(); // (refreshing would clear what the server just refused)
    else await refresh();
  }

  function draw() {
    const team = roster.team(roster.teamId);
    replace(pick, roster.teams.map((each) => h("option", { value: each.id, selected: each.id === roster.teamId }, each.name)));
    if (!team) {
      replace(status, h("p", { class: "muted" }, t("party.no_team")));
      return;
    }
    const members = team.members.length ? team.members.map((member) => member.name).join(", ") : t("teams.empty");
    const playing = h("button", { type: "button", class: "primary", id: "party-play", disabled: team.members.length < roster.rules.team_min, onclick: () => act("play") }, t("party.play"));
    const parts = [h("p", {}, t("party.members", { team: team.name, members })), playing];
    if (town?.in_town) {
      parts.push(
        h("h3", {}, t("party.town")),
        h(
          "ul",
          { class: "rows", "aria-label": t("party.town_teams") },
          town.teams.map((each) =>
            h("li", { class: "row" }, `${teamName(each.team)}: ${t(each.waiting ? "party.is_waiting" : "party.is_in_town")}`),
          ),
        ),
      );
      const here = town.teams.find((each) => each.team === team.id);
      parts.push(
        h(
          "div",
          { class: "row-actions" },
          here?.waiting
            ? h("button", { type: "button", id: "party-come-back", onclick: () => act("town/come-back", "party.came_back") }, t("party.come_back"))
            : h("button", { type: "button", class: "primary", id: "party-ready", onclick: () => act("town/ready") }, t("party.ready")),
          h("button", { type: "button", id: "party-leave", onclick: () => act("town/leave-party", "party.left") }, t("party.leave")),
        ),
      );
    } else if (town) {
      parts.push(h("p", { class: "muted" }, t("party.not_in_town")));
    }
    replace(status, ...parts);
  }

  roster.onChange(() => {
    if (roster.teamId !== shown) refresh();
    else draw();
  });
  draw();

  const element = h(
    "div",
    { class: "panel", id: "panel-party" },
    h("h2", {}, t("panel.party")),
    h("div", { class: "field" }, h("label", { for: "party-team" }, t("party.team")), pick),
    status,
    problem,
    note,
    h("button", { type: "button", onclick: refresh }, t("panel.refresh")),
  );
  return { element, show: refresh };
}

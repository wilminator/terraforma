// The teams panel: the player's teams (each opens its own page, where its heroes are made and changed) and the form that makes a team.
// A team is made with a name first; its heroes come on its page. A team with fewer heroes than the game's minimum is incomplete: it
// cannot play until it is filled.

import { h, replace } from "./dom.js";
import { attempt, field } from "./forms.js";

export function teamsPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const list = h("ul", { class: "rows", "aria-label": t("teams.list") });
  const makeHost = h("div", {});

  function row(team) {
    const { team_min: least, team_max: most } = roster.rules;
    return h(
      "li",
      { class: "row", "data-team": team.name },
      h("div", { class: "row-main" }, h("strong", {}, team.name), h("span", { class: "muted small" }, ` ${t("teams.heroes_count", { count: team.members.length, most })}`)),
      team.members.length < least ? h("p", { class: "muted small", role: "status" }, t("teams.needs_to_play", { least, count: team.members.length })) : null,
      h("p", { class: "muted small" }, team.members.map((member) => member.name).join(", ")),
      h("div", { class: "row-actions" }, h("a", { class: "button", href: `/team/${team.id}`, "aria-label": t("teams.open_label", { name: team.name }) }, t("teams.open"))),
    );
  }

  function makeForm() {
    const name = field("team-name", t("teams.name"), { maxlength: "24", autocomplete: "off", required: true });
    return h(
      "form",
      {
        class: "card",
        id: "team-form",
        onsubmit: (event) => {
          event.preventDefault();
          attempt(shell, problem, async () => {
            const team = await roster.saveTeam(name.input.value.trim(), []);
            location.assign(`/team/${team.id}`); // its heroes are made on its own page
          });
        },
      },
      h("h3", {}, t("teams.new")),
      name.row,
      h("p", { class: "muted small" }, t("teams.new_hint")),
      h("button", { type: "submit", class: "primary", id: "team-save" }, t("teams.create")),
    );
  }

  function draw() {
    replace(list, roster.teams.length ? roster.teams.map(row) : h("li", { class: "muted" }, t("teams.none")));
    const used = roster.teams.length;
    replace(
      makeHost,
      h("p", { class: "muted small", id: "team-count" }, t("teams.count", { used, most: roster.rules.max_teams })),
      used < roster.rules.max_teams ? makeForm() : null,
    );
  }

  // (a rebuilt form would lose what is being typed, so a change to the roster redraws only while the form is not in use)
  roster.onChange(() => {
    if (!makeHost.contains(document.activeElement) || document.activeElement === document.body) draw();
    else replace(list, roster.teams.length ? roster.teams.map(row) : h("li", { class: "muted" }, t("teams.none")));
  });
  draw();
  return h("div", { class: "panel", id: "panel-teams" }, h("h2", {}, t("panel.teams")), list, problem, makeHost);
}

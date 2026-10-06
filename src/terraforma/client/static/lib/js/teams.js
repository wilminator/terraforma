// The teams panel: the player's teams and who is on each, with a form to make one and rename, delete, add and remove on them.

import { h, replace } from "./dom.js";
import { attempt, field } from "./forms.js";

export function teamsPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const list = h("ul", { class: "rows", "aria-label": t("teams.list") });
  let editing = null; // the team being renamed
  let confirming = null; // the team whose delete is waiting for a second press

  const name = field("team-name", t("teams.name"), { maxlength: "24", autocomplete: "off", required: true });
  const form = h(
    "form",
    {
      class: "card",
      id: "team-form",
      onsubmit: (event) => {
        event.preventDefault();
        attempt(shell, problem, async () => {
          await roster.createTeam(name.input.value.trim());
          name.input.value = "";
        });
      },
    },
    h("h3", {}, t("teams.new")),
    name.row,
    h("button", { type: "submit", class: "primary" }, t("teams.create")),
  );

  /** The heroes that are on no team, who can be added to one. */
  const free = () => roster.heroes.filter((hero) => roster.teamOf(hero.id) === null);

  function adder(team) {
    const heroes = free();
    if (!heroes.length) return h("p", { class: "muted small" }, t("teams.nobody_free"));
    const pick = h("select", { "aria-label": t("teams.add_label", { team: team.name }) }, heroes.map((hero) => h("option", { value: hero.id }, hero.name)));
    return h(
      "div",
      { class: "inline" },
      pick,
      h("button", { type: "button", onclick: () => attempt(shell, problem, () => roster.addToTeam(team.id, Number(pick.value))) }, t("teams.add")),
    );
  }

  function row(team) {
    const title =
      editing === team.id
        ? h(
            "form",
            {
              class: "inline",
              onsubmit: (event) => {
                event.preventDefault();
                const input = event.currentTarget.elements.rename;
                attempt(shell, problem, async () => {
                  await roster.renameTeam(team.id, input.value.trim());
                  editing = null;
                  draw();
                });
              },
            },
            h("input", { name: "rename", value: team.name, maxlength: "24", "aria-label": t("teams.new_name_label", { name: team.name }), autocomplete: "off" }),
            h("button", { type: "submit" }, t("panel.save")),
            h("button", { type: "button", onclick: () => { editing = null; draw(); } }, t("panel.cancel")),
          )
        : h("strong", {}, team.name);
    const members = team.members.length
      ? h(
          "ol",
          { class: "members", "aria-label": t("teams.members_label", { name: team.name }) },
          team.members.map((member) =>
            h(
              "li",
              {},
              member.name,
              h("button", { type: "button", "aria-label": t("teams.remove_label", { name: member.name, team: team.name }), onclick: () => attempt(shell, problem, () => roster.removeFromTeam(team.id, member.hero_id)) }, t("teams.remove")),
            ),
          ),
        )
      : h("p", { class: "muted small" }, t("teams.empty"));
    return h(
      "li",
      { class: "row", "data-team": team.name },
      h("div", { class: "row-main" }, title),
      members,
      adder(team),
      h(
        "div",
        { class: "row-actions" },
        h("button", { type: "button", "aria-label": t("teams.rename_label", { name: team.name }), onclick: () => { editing = team.id; draw(); } }, t("panel.rename")),
        h(
          "button",
          {
            type: "button",
            class: confirming === team.id ? "danger" : null,
            "aria-label": t("teams.delete_label", { name: team.name }),
            onclick: () => {
              if (confirming !== team.id) {
                confirming = team.id;
                draw();
                return;
              }
              confirming = null;
              attempt(shell, problem, () => roster.deleteTeam(team.id));
            },
          },
          t(confirming === team.id ? "panel.sure" : "panel.delete"),
        ),
      ),
    );
  }

  function draw() {
    replace(list, roster.teams.length ? roster.teams.map(row) : h("li", { class: "muted" }, t("teams.none")));
  }

  roster.onChange(draw);
  draw();
  return h("div", { class: "panel", id: "panel-teams" }, h("h2", {}, t("panel.teams")), list, problem, form);
}

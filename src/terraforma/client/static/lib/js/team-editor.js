// A team's own page content: its name, its heroes (rename, replace, remove, move or exchange) and a form that fills an empty place.
// What may change about a saved team's heroes is the game's to allow, so the buttons are always offered and the server's answer says
// no, in its own words, when the game refuses.

import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

const statsLine = (stats) => Object.entries(stats).filter(([, value]) => value).map(([name, value]) => `${name} ${value}`).join(", ");

/** Draws the team $teamId from $roster into the returned element, and again whenever the roster changes. $onGone runs if the team is deleted. */
export function teamEditor(shell, roster, teamId, onGone) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const root = h("section", { class: "team-editor", id: "team-editor" });
  let editingTeam = false; // the team is being renamed
  let editingHero = null; // the hero being renamed
  let replacing = null; // the hero being replaced
  let confirming = false; // deleting the team waits for a second press

  const jobName = (key) => roster.jobs.find((each) => each.key === key)?.name ?? key;
  const jobSelect = (id, label, chosen) =>
    h(
      "select",
      { id, "aria-label": label },
      roster.jobs.length ? roster.jobs.map((each) => h("option", { value: each.key, selected: each.key === chosen }, each.name)) : h("option", { value: "" }, t("teams.no_jobs")),
    );

  /** One select for everything that changes which team a hero is on: a move to another team, or an exchange with one of its heroes. */
  function changer(hero, team) {
    const options = [];
    for (const other of roster.teams.filter((each) => each.id !== team.id)) {
      if (other.members.length < roster.rules.team_max) options.push(h("option", { value: `move:${other.id}` }, t("teams.move_to", { team: other.name })));
      for (const member of other.members) options.push(h("option", { value: `swap:${member.hero_id}` }, t("teams.swap_with", { hero: member.name, team: other.name })));
    }
    if (!options.length) return null;
    const pick = h("select", { "aria-label": t("teams.change_label", { name: hero.name }) }, options);
    return h(
      "div",
      { class: "inline" },
      pick,
      h(
        "button",
        {
          type: "button",
          "aria-label": t("teams.change_go", { name: hero.name }),
          onclick: () => {
            const [how, id] = pick.value.split(":");
            attempt(shell, problem, () => (how === "move" ? roster.moveHero(hero.id, Number(id)) : roster.swapHeroes(hero.id, Number(id))));
          },
        },
        t("teams.change"),
      ),
    );
  }

  function heroRow(member, team) {
    const hero = roster.hero(member.hero_id);
    if (!hero) return null;
    if (editingHero === hero.id) {
      return h(
        "li",
        { class: "hero" },
        h(
          "form",
          {
            class: "inline",
            onsubmit: (event) => {
              event.preventDefault();
              attempt(shell, problem, async () => {
                await roster.renameHero(hero.id, event.currentTarget.elements.rename.value.trim());
                editingHero = null;
                draw();
              });
            },
          },
          h("input", { name: "rename", value: hero.name, maxlength: "24", "aria-label": t("teams.new_name_label", { name: hero.name }), autocomplete: "off" }),
          h("button", { type: "submit" }, t("panel.save")),
          h("button", { type: "button", onclick: () => { editingHero = null; draw(); } }, t("panel.cancel")),
        ),
      );
    }
    if (replacing === hero.id) {
      const name = h("input", { name: "replacement", maxlength: "24", "aria-label": t("teams.replacement_name", { name: hero.name }), autocomplete: "off", required: true });
      const job = jobSelect(`replace-job-${hero.id}`, t("teams.replacement_job", { name: hero.name }), hero.job);
      return h(
        "li",
        { class: "hero" },
        h(
          "form",
          {
            class: "inline",
            onsubmit: (event) => {
              event.preventDefault();
              attempt(shell, problem, async () => {
                await roster.replaceHero(team.id, hero.id, name.value.trim(), job.value);
                replacing = null;
                draw();
              });
            },
          },
          name,
          job,
          h("button", { type: "submit" }, t("teams.replace")),
          h("button", { type: "button", onclick: () => { replacing = null; draw(); } }, t("panel.cancel")),
        ),
      );
    }
    return h(
      "li",
      { class: "hero", "data-hero": hero.name },
      h("div", { class: "row-main" }, h("strong", {}, hero.name), h("span", { class: "muted" }, ` ${jobName(hero.job)}, ${t("teams.level", { level: hero.level })}`)),
      h("div", { class: "muted small" }, `${statsLine(hero.stats)} · ${t("teams.place", { map: hero.place.map, x: hero.place.x, y: hero.place.y })}`),
      h(
        "div",
        { class: "row-actions" },
        h("button", { type: "button", "aria-label": t("teams.rename_hero", { name: hero.name }), onclick: () => { editingHero = hero.id; draw(); } }, t("panel.rename")),
        h("button", { type: "button", "aria-label": t("teams.replace_hero", { name: hero.name }), onclick: () => { replacing = hero.id; draw(); } }, t("teams.replace")),
        h("button", { type: "button", "aria-label": t("teams.remove_hero", { name: hero.name, team: team.name }), onclick: () => attempt(shell, problem, () => roster.removeHero(team.id, hero.id)) }, t("teams.remove")),
      ),
      changer(hero, team),
    );
  }

  /** The form that fills an empty place on the team. */
  function adder(team) {
    const name = h("input", { id: "new-hero", name: "new-hero", maxlength: "24", "aria-label": t("teams.add_name", { team: team.name }), autocomplete: "off", required: true });
    const job = jobSelect("new-hero-job", t("teams.add_job", { team: team.name }), roster.jobs[0]?.key);
    return h(
      "form",
      {
        class: "card",
        id: "add-hero-form",
        onsubmit: (event) => {
          event.preventDefault();
          attempt(shell, problem, async () => {
            await roster.addHero(team.id, name.value.trim(), job.value);
            name.value = "";
          });
        },
      },
      h("h3", {}, t("teams.add_title")),
      h("div", { class: "inline" }, name, job, h("button", { type: "submit", class: "primary" }, t("teams.add"))),
    );
  }

  function draw() {
    const team = roster.team(teamId);
    if (!team) return onGone?.();
    const { team_min: least, team_max: most } = roster.rules;
    const title = editingTeam
      ? h(
          "form",
          {
            class: "inline",
            onsubmit: (event) => {
              event.preventDefault();
              attempt(shell, problem, async () => {
                await roster.renameTeam(team.id, event.currentTarget.elements.rename.value.trim());
                editingTeam = false;
                draw();
              });
            },
          },
          h("input", { name: "rename", value: team.name, maxlength: "24", "aria-label": t("teams.new_team_name_label", { name: team.name }), autocomplete: "off" }),
          h("button", { type: "submit" }, t("panel.save")),
          h("button", { type: "button", onclick: () => { editingTeam = false; draw(); } }, t("panel.cancel")),
        )
      : h("h1", { id: "team-title" }, team.name);
    replace(
      root,
      title,
      h("p", { class: "muted", id: "team-count" }, t("teams.heroes_count", { count: team.members.length, most })),
      team.members.length < least ? h("p", { class: "note", role: "status", id: "team-incomplete" }, t("teams.needs_to_play", { least, count: team.members.length })) : null,
      h("ul", { class: "rows", "aria-label": t("teams.members_label", { name: team.name }) }, team.members.map((member) => heroRow(member, team))),
      team.members.length < most ? adder(team) : h("p", { class: "muted small" }, t("teams.full")),
      problem,
      h(
        "div",
        { class: "row-actions" },
        h("button", { type: "button", "aria-label": t("teams.rename_label", { name: team.name }), onclick: () => { editingTeam = true; draw(); } }, t("panel.rename")),
        h(
          "button",
          {
            type: "button",
            class: confirming ? "danger" : null,
            "aria-label": t("teams.delete_label", { name: team.name }),
            onclick: () => {
              if (!confirming) {
                confirming = true;
                draw();
                return;
              }
              confirming = false;
              attempt(shell, problem, () => roster.disbandTeam(team.id));
            },
          },
          t(confirming ? "teams.sure" : "panel.delete"),
        ),
      ),
    );
  }

  roster.onChange(draw);
  draw();
  return root;
}

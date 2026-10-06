// The teams panel: the player's teams, each with its heroes, and the form that makes a team with its heroes in one go.
// A hero is always on a team. What may change about a saved team's heroes (removing, replacing, moving) is the game's to
// allow, so those buttons are always offered and the server's answer says no, in its own words, when the game refuses.

import { h, replace } from "./dom.js";
import { attempt, field } from "./forms.js";

const statsLine = (stats) => Object.entries(stats).filter(([, value]) => value).map(([name, value]) => `${name} ${value}`).join(", ");

export function teamsPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const list = h("ul", { class: "rows", "aria-label": t("teams.list") });
  const makeHost = h("div", {});
  let editingTeam = null; // the team being renamed
  let editingHero = null; // the hero being renamed
  let replacing = null; // the hero being replaced
  let confirming = null; // what is waiting for a second press: "team:<id>"
  let draft = [{ name: "", job: "" }]; // the heroes of the team being made
  let draftName = ""; // and its name

  const jobName = (key) => roster.jobs.find((each) => each.key === key)?.name ?? key;
  const jobSelect = (id, label, chosen) =>
    h(
      "select",
      { id, "aria-label": label },
      roster.jobs.length ? roster.jobs.map((each) => h("option", { value: each.key, selected: each.key === chosen }, each.name)) : h("option", { value: "" }, t("teams.no_jobs")),
    );

  // --- the form that makes a team ----------------------------------------------------------------

  function makeForm() {
    const { team_min: least, team_max: most } = roster.rules;
    while (draft.length < Math.max(least, 1)) draft.push({ name: "", job: roster.jobs[0]?.key ?? "" });
    draft.length = Math.min(draft.length, most);
    const name = field("team-name", t("teams.name"), { maxlength: "24", autocomplete: "off", required: true, value: draftName });
    name.input.addEventListener("input", () => { draftName = name.input.value; check(); });
    const rows = draft.map((each, number) => {
      const input = h("input", { id: `new-hero-${number + 1}`, "aria-label": t("teams.hero_name", { number: number + 1 }), maxlength: "24", autocomplete: "off", value: each.name });
      input.addEventListener("input", () => { each.name = input.value; check(); });
      const job = jobSelect(`new-job-${number + 1}`, t("teams.hero_job", { number: number + 1 }), each.job || roster.jobs[0]?.key);
      each.job = job.value;
      job.addEventListener("change", () => { each.job = job.value; });
      return h(
        "div",
        { class: "inline hero-draft" },
        input,
        job,
        draft.length > least
          ? h("button", { type: "button", "aria-label": t("teams.drop_row", { number: number + 1 }), onclick: () => { draft.splice(number, 1); drawMake(); } }, t("teams.remove"))
          : null,
      );
    });
    const save = h("button", { type: "submit", class: "primary", id: "team-save" }, t("teams.save"));
    const status = h("p", { class: "muted small", role: "status" });
    function check() {
      const filled = draft.filter((each) => each.name.trim()).length;
      const ready = Boolean(draftName.trim()) && filled === draft.length && draft.length >= least && roster.jobs.length > 0;
      save.disabled = !ready;
      status.textContent = draft.length < least || filled < least ? t("teams.needs", { least, most }) : "";
    }
    const form = h(
      "form",
      {
        class: "card",
        id: "team-form",
        onsubmit: (event) => {
          event.preventDefault();
          attempt(shell, problem, async () => {
            await roster.saveTeam(name.input.value.trim(), draft.map((each) => ({ name: each.name.trim(), job: each.job })));
            draft = [{ name: "", job: "" }];
            draftName = "";
            drawMake();
          });
        },
      },
      h("h3", {}, t("teams.new")),
      name.row,
      ...rows,
      draft.length < most ? h("button", { type: "button", id: "add-draft-hero", onclick: () => { draft.push({ name: "", job: roster.jobs[0]?.key ?? "" }); drawMake(); } }, t("teams.more_heroes")) : null,
      status,
      save,
    );
    check();
    return form;
  }

  function drawMake() {
    const used = roster.teams.length;
    const note = h("p", { class: "muted small", id: "team-count" }, t("teams.count", { used, most: roster.rules.max_teams }));
    replace(makeHost, note, used < roster.rules.max_teams ? makeForm() : null);
  }

  // --- a team and its heroes ---------------------------------------------------------------------

  /** One select for everything that changes which team a hero is on: a move to another team, or an exchange with one of its heroes. */
  function changer(hero, team) {
    const others = roster.teams.filter((each) => each.id !== team.id);
    const options = [];
    for (const other of others) {
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
      h("div", { class: "muted small" }, `${statsLine(hero.stats)} Â· ${t("teams.place", { map: hero.place.map, x: hero.place.x, y: hero.place.y })}`),
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

  /** The form that fills an empty place on a team. */
  function adder(team) {
    const name = h("input", { name: "new-hero", maxlength: "24", "aria-label": t("teams.add_name", { team: team.name }), autocomplete: "off", required: true });
    const job = jobSelect(`add-job-${team.id}`, t("teams.add_job", { team: team.name }), roster.jobs[0]?.key);
    return h(
      "form",
      {
        class: "inline",
        onsubmit: (event) => {
          event.preventDefault();
          attempt(shell, problem, async () => {
            await roster.addHero(team.id, name.value.trim(), job.value);
            name.value = "";
          });
        },
      },
      name,
      job,
      h("button", { type: "submit" }, t("teams.add")),
    );
  }

  function row(team) {
    const { team_min: least, team_max: most } = roster.rules;
    const title =
      editingTeam === team.id
        ? h(
            "form",
            {
              class: "inline",
              onsubmit: (event) => {
                event.preventDefault();
                attempt(shell, problem, async () => {
                  await roster.renameTeam(team.id, event.currentTarget.elements.rename.value.trim());
                  editingTeam = null;
                  draw();
                });
              },
            },
            h("input", { name: "rename", value: team.name, maxlength: "24", "aria-label": t("teams.new_team_name_label", { name: team.name }), autocomplete: "off" }),
            h("button", { type: "submit" }, t("panel.save")),
            h("button", { type: "button", onclick: () => { editingTeam = null; draw(); } }, t("panel.cancel")),
          )
        : h("strong", {}, team.name);
    const key = `team:${team.id}`;
    const short = team.members.length < least;
    return h(
      "li",
      { class: "row", "data-team": team.name },
      h("div", { class: "row-main" }, title, h("span", { class: "muted small" }, ` ${t("teams.heroes_count", { count: team.members.length, most })}`)),
      short ? h("p", { class: "muted small", role: "status" }, t("teams.needs_to_play", { least })) : null,
      h("ul", { class: "rows", "aria-label": t("teams.members_label", { name: team.name }) }, team.members.map((member) => heroRow(member, team))),
      team.members.length < most ? adder(team) : null,
      h(
        "div",
        { class: "row-actions" },
        h("button", { type: "button", "aria-label": t("teams.rename_label", { name: team.name }), onclick: () => { editingTeam = team.id; draw(); } }, t("panel.rename")),
        h(
          "button",
          {
            type: "button",
            class: confirming === key ? "danger" : null,
            "aria-label": t("teams.delete_label", { name: team.name }),
            onclick: () => {
              if (confirming !== key) {
                confirming = key;
                draw();
                return;
              }
              confirming = null;
              attempt(shell, problem, () => roster.disbandTeam(team.id));
            },
          },
          t(confirming === key ? "teams.sure" : "panel.delete"),
        ),
      ),
    );
  }

  function draw() {
    replace(list, roster.teams.length ? roster.teams.map(row) : h("li", { class: "muted" }, t("teams.none")));
    const open = makeHost.contains(document.activeElement); // (a keystroke in the form must not rebuild the form under it)
    if (!open) drawMake();
  }

  roster.onChange(draw);
  draw();
  return h("div", { class: "panel", id: "panel-teams" }, h("h2", {}, t("panel.teams")), list, problem, makeHost);
}

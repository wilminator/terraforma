// The heroes panel: the player's heroes, with a form to make a new one, and rename and delete on each.

import { h, replace } from "./dom.js";
import { attempt, field } from "./forms.js";

const statsLine = (stats) => Object.entries(stats).filter(([, value]) => value).map(([name, value]) => `${name} ${value}`).join(", ");

export function heroesPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const list = h("ul", { class: "rows", "aria-label": t("heroes.list") });
  let editing = null; // the hero being renamed
  let confirming = null; // the hero whose delete is waiting for a second press

  const name = field("hero-name", t("heroes.name"), { maxlength: "24", autocomplete: "off", required: true });
  const job = h("select", { id: "hero-job", name: "hero-job" });
  const create = h("button", { type: "submit", class: "primary" }, t("heroes.create"));
  const form = h(
    "form",
    {
      class: "card",
      id: "hero-form",
      onsubmit: (event) => {
        event.preventDefault();
        attempt(shell, problem, async () => {
          await roster.createHero(name.input.value.trim(), job.value);
          name.input.value = "";
        });
      },
    },
    h("h3", {}, t("heroes.new")),
    name.row,
    h("div", { class: "field" }, h("label", { for: "hero-job" }, t("heroes.job")), job),
    create,
  );

  const jobName = (key) => roster.jobs.find((each) => each.key === key)?.name ?? key;

  function row(hero) {
    const chosen = roster.heroId === hero.id;
    const title =
      editing === hero.id
        ? h(
            "form",
            {
              class: "inline",
              onsubmit: (event) => {
                event.preventDefault();
                const input = event.currentTarget.elements.rename;
                attempt(shell, problem, async () => {
                  await roster.renameHero(hero.id, input.value.trim());
                  editing = null;
                  draw();
                });
              },
            },
            h("input", { name: "rename", value: hero.name, maxlength: "24", "aria-label": t("heroes.new_name_label", { name: hero.name }), autocomplete: "off" }),
            h("button", { type: "submit" }, t("panel.save")),
            h("button", { type: "button", onclick: () => { editing = null; draw(); } }, t("panel.cancel")),
          )
        : h("strong", {}, hero.name);
    return h(
      "li",
      { class: chosen ? "row chosen" : "row", "data-hero": hero.name },
      h("div", { class: "row-main" }, title, h("span", { class: "muted" }, ` ${jobName(hero.job)}, ${t("heroes.level", { level: hero.level })}`)),
      h("div", { class: "muted small" }, `${statsLine(hero.stats)} · ${t("heroes.place", { map: hero.place.map, x: hero.place.x, y: hero.place.y })}`),
      h(
        "div",
        { class: "row-actions" },
        h("button", { type: "button", "aria-pressed": String(chosen), onclick: () => roster.selectHero(hero.id) }, t(chosen ? "heroes.chosen" : "heroes.choose")),
        h("button", { type: "button", "aria-label": t("heroes.rename_label", { name: hero.name }), onclick: () => { editing = hero.id; draw(); } }, t("panel.rename")),
        h(
          "button",
          {
            type: "button",
            class: confirming === hero.id ? "danger" : null,
            "aria-label": t("heroes.delete_label", { name: hero.name }),
            onclick: () => {
              if (confirming !== hero.id) {
                confirming = hero.id;
                draw();
                return;
              }
              confirming = null;
              attempt(shell, problem, () => roster.deleteHero(hero.id));
            },
          },
          t(confirming === hero.id ? "panel.sure" : "panel.delete"),
        ),
      ),
    );
  }

  function draw() {
    const kept = job.value;
    replace(job, roster.jobs.length ? roster.jobs.map((each) => h("option", { value: each.key }, each.name)) : h("option", { value: "" }, t("heroes.no_jobs")));
    if (roster.jobs.some((each) => each.key === kept)) job.value = kept;
    create.disabled = !roster.jobs.length;
    replace(list, roster.heroes.length ? roster.heroes.map(row) : h("li", { class: "muted" }, t("heroes.none")));
  }

  roster.onChange(draw);
  draw();
  return h("div", { class: "panel", id: "panel-heroes" }, h("h2", {}, t("panel.heroes")), list, problem, form);
}

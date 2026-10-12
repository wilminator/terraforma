// How the alliance sees other teams and alliances: a score on the game's scale (named by its bands) and a private note. Members
// read them; a role that may speak for the alliance sets and forgets them.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt, field, formCard } from "./forms.js";

export async function relationsCard(shell, ctx) {
  const t = (key, values) => shell.text.get(key, values);
  const base = `/api/alliances/${ctx.id}/relationships`;
  const box = h("section", { class: "card", id: "relationships" });
  const problem = h("p", { class: "problem", role: "alert" });
  let rows = [];

  const reload = async () => {
    rows = (await api.get(base)).relationships;
    draw();
  };

  const row = (each) => h(
    "li",
    { class: "row", "data-relationship": each.name },
    h("div", { class: "row-main" }, h("strong", {}, each.name), " ", h("span", { class: "muted small" }, `${t("relations." + each.kind)} · ${t("relations.band", { band: each.band, score: each.score })}`)),
    each.note ? h("p", {}, each.note) : null,
    ctx.can.has("speak")
      ? h("div", { class: "row-actions" }, h("button", { type: "button", "aria-label": t("relations.forget_label", { name: each.name }), onclick: () => attempt(shell, problem, async () => {
        await api.post(`${base}/forget`, { kind: each.kind, id: each.id });
        await reload();
      }) }, t("relations.forget")))
      : null,
  );

  function setForm() {
    const kind = h("select", { id: "relation-kind" }, h("option", { value: "team" }, t("relations.team")), h("option", { value: "alliance" }, t("relations.alliance")));
    const number = field("relation-number", t("relations.number"), { type: "number", min: "1", required: true });
    const score = field("relation-score", t("relations.score"), { type: "number", min: "-100", max: "100" });
    const note = field("relation-note", t("relations.note"), { maxlength: "500", autocomplete: "off" });
    return formCard(shell, {
      id: "relation-form",
      rows: [h("div", { class: "field" }, h("label", { for: "relation-kind" }, t("relations.kind")), kind), number.row, score.row, note.row],
      submit: "relations.save",
      run: async () => {
        const body = { kind: kind.value, id: Number(number.input.value) };
        if (score.input.value !== "") body.score = Number(score.input.value);
        if (note.input.value !== "") body.note = note.input.value;
        await api.post(`${base}/set`, body);
        await reload();
      },
    }).form;
  }

  function draw() {
    replace(
      box,
      h("h2", {}, t("relations.title")),
      h("p", { class: "hint" }, t("relations.hint")),
      rows.length ? h("ul", { class: "rows", "aria-label": t("relations.title") }, rows.map(row)) : h("p", { class: "muted" }, t("relations.none")),
      problem,
      ctx.can.has("speak") ? setForm() : null,
    );
  }

  await reload();
  return box;
}

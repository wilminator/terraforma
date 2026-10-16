// The admin's monsters: look at the game's monsters with their strength (pxp), write new ones, rewrite, retire and restore the ones written here
// (content/routes.py). One section of the admin page. A monster from the seed can be read and copied but is changed in the seed.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt, field, formCard } from "./forms.js";
import { area, formatKeys, formatNumbers, parseKeys, parseNumbers, parseWhole } from "./admin-fields.js";

const WHICH = ["all", "authored", "seed", "retired"];
const PAGE = 50;

/** The query that narrows the list to $which. */
const narrow = (which) => ({ all: "", authored: "&authored=true", seed: "&authored=false", retired: "&active=false" })[which];

export function monstersSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "monsters-problem" });
  const holder = h("div", { class: "stack", id: "monsters" });
  let which = "all";

  async function list() {
    const rows = h("ul", { class: "rows", id: "monster-list", "aria-label": t("admin.monsters") });
    const more = h("p", {});
    const none = h("p", { class: "muted", id: "monster-none", hidden: true }, t("admin.monsters_none"));
    const fetchPage = async (after) => {
      const found = (await api.get(`/api/admin/monsters?limit=${PAGE}&after=${after}${narrow(which)}`)).monsters;
      rows.append(...found.map(row));
      none.hidden = rows.childElementCount > 0;
      replace(more, found.length === PAGE ? h("button", { type: "button", id: "monster-more", onclick: () => attempt(shell, problem, () => fetchPage(found.at(-1).id)) }, t("admin.more")) : null);
    };
    replace(
      holder,
      h("div", { class: "tabs", role: "group", "aria-label": t("admin.monsters_which") }, WHICH.map((each) =>
        h("button", { type: "button", "aria-pressed": String(each === which), onclick: () => attempt(shell, problem, () => { which = each; return list(); }) }, t(`admin.which_${each}`)))),
      h("p", {}, h("button", { type: "button", id: "monster-new", class: "primary", onclick: () => form(null, false) }, t("admin.monster_new"))),
      rows,
      none,
      more,
    );
    await fetchPage(0);
  }

  const setActive = (monster, verb) => () => attempt(shell, problem, async () => {
    await api.post(`/api/admin/monsters/${encodeURIComponent(monster.key)}/${verb}`);
    await list();
  });

  function row(monster) {
    const actions = [h("button", { type: "button", "aria-label": t("admin.monster_copy_label", { name: monster.name }), onclick: () => form(monster, true) }, t("admin.monster_copy"))];
    if (monster.authored) {
      actions.unshift(h("button", { type: "button", "aria-label": t("admin.monster_edit_label", { name: monster.name }), onclick: () => form(monster, false) }, t("admin.monster_edit")));
      actions.push(monster.active
        ? h("button", { type: "button", "aria-label": t("admin.monster_retire_label", { name: monster.name }), onclick: setActive(monster, "retire") }, t("admin.monster_retire"))
        : h("button", { type: "button", "aria-label": t("admin.monster_restore_label", { name: monster.name }), onclick: setActive(monster, "restore") }, t("admin.monster_restore")));
    }
    return h(
      "li",
      { class: "row", "data-monster": monster.key, "data-authored": String(monster.authored), "data-active": String(monster.active) },
      h("div", { class: "row-main" },
        h("strong", {}, monster.name), " ",
        h("span", { class: "muted small" }, t("admin.monster_line", { key: monster.key, personality: monster.personality, pxp: monster.pxp, source: t(monster.authored ? "admin.monster_authored" : "admin.monster_seed"), state: t(monster.active ? "admin.monster_active" : "admin.monster_retired") }))),
      h("div", { class: "row-actions" }, ...actions),
    );
  }

  /** The form that writes a monster: a new one (from $monster, if it is a copy), or a rewrite of an authored one ($copy false). */
  function form(monster, copy) {
    const rewrite = monster !== null && !copy;
    const key = field("monster-key", t("admin.monster_key"), { maxlength: "64", required: true, autocomplete: "off", value: rewrite ? monster.key : "" });
    if (rewrite) key.input.readOnly = true;
    const name = field("monster-name", t("admin.monster_name"), { maxlength: "64", required: true, autocomplete: "off", value: monster?.name ?? "" });
    const personality = field("monster-personality", t("admin.monster_personality"), { maxlength: "64", required: true, autocomplete: "off", value: monster?.personality ?? "" });
    const xp = field("monster-xp", t("admin.monster_xp"), { inputmode: "numeric", autocomplete: "off", value: String(monster?.xp_reward ?? 0) });
    const gold = field("monster-gold", t("admin.monster_gold"), { inputmode: "numeric", autocomplete: "off", value: String(monster?.gold_reward ?? 0) });
    const stats = area("monster-stats", t("admin.monster_stats"), { rows: "7", spellcheck: "false" });
    stats.input.value = formatNumbers(monster?.stats);
    const ai = area("monster-ai", t("admin.monster_ai"), { rows: "4", spellcheck: "false" });
    ai.input.value = formatNumbers(monster?.ai);
    const lists = ["abilities", "items", "equipment", "drops"].map((name) => {
      const box = field(`monster-${name}`, t(`admin.monster_${name}`), { autocomplete: "off", value: formatKeys(monster?.[name]) });
      return { name, ...box };
    });
    const card = formCard(shell, {
      id: "monster-form",
      title: rewrite ? "admin.monster_edit_title" : "admin.monster_new",
      rows: [key.row, name.row, personality.row, xp.row, gold.row, stats.row, ...lists.map((each) => each.row), ai.row, h("p", { class: "hint" }, t("admin.monster_hint"))],
      submit: "admin.monster_save",
      run: async () => {
        const body = {
          name: name.input.value.trim(),
          personality: personality.input.value.trim(),
          xp_reward: parseWhole(xp.input.value, t("admin.monster_xp"), shell, 0),
          gold_reward: parseWhole(gold.input.value, t("admin.monster_gold"), shell, 0),
          stats: parseNumbers(stats.input.value, shell),
          ai: parseNumbers(ai.input.value, shell),
          ...Object.fromEntries(lists.map((each) => [each.name, parseKeys(each.input.value)])),
        };
        if (rewrite) await api.put(`/api/admin/monsters/${encodeURIComponent(monster.key)}`, body);
        else await api.post("/api/admin/monsters", { key: key.input.value.trim(), ...body });
        await list();
      },
    });
    replace(holder, h("p", {}, h("button", { type: "button", id: "monster-back", onclick: () => attempt(shell, problem, list) }, t("admin.back"))), card.form);
  }

  const element = h("section", { id: "admin-monsters", "aria-labelledby": "admin-monsters-title" }, h("h2", { id: "admin-monsters-title" }, t("admin.monsters")), holder, problem);
  return { element, load: () => attempt(shell, problem, list) };
}

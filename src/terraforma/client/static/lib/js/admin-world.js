// The living world's realms and settlements (realms/routes.py): an admin sees each realm's strength and moves it, lets a live settlement fall or
// founds one that is dormant or in ruins, and looks at a settlement's supplies and cuts the sources of one (0 puts them back).

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function worldSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "world-problem" });
  const note = h("p", { class: "note", role: "status", id: "world-note" });
  const realms = h("ul", { class: "rows", id: "world-realms", "aria-label": t("admin.world_realms") });
  const towns = h("ul", { class: "rows", id: "world-settlements", "aria-label": t("admin.world_settlements") });
  const none = h("p", { class: "muted", id: "world-none", hidden: true }, t("admin.world_none"));
  let names = {}; // realm id -> name

  /** A change the server accepted: $done says what happened, and the row shows the server's answer. */
  function change(run, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      note.textContent = done(await run());
    });
  }

  function realmRow(realm) {
    const input = h("input", { type: "number", step: "1", value: String(realm.strength), "aria-label": t("admin.world_strength_name", { name: realm.name }) });
    const row = h(
      "li",
      { class: "row", "data-realm": realm.key },
      h("div", {}, h("strong", {}, realm.name), h("span", { class: "muted small" }, ` ${t("admin.world_strength", { strength: realm.strength })}`)),
      h("div", { class: "inline" }, input, h("button", { type: "button", "aria-label": t("admin.world_set_name", { name: realm.name }), onclick: () => change(async () => {
        const { realm: now } = await api.post(`/api/admin/realms/${realm.id}/strength`, { strength: Number(input.value) });
        row.replaceWith(realmRow(now));
        return now;
      }, (now) => t("admin.world_set_done", { name: now.name, strength: now.strength })) }, t("admin.world_set"))),
    );
    return row;
  }

  function settlementRow(town) {
    const supplies = h("ul", { class: "rows", "aria-label": t("admin.world_supplies_name", { name: town.name }) });
    const action = town.state === "live" ? ["fall", "admin.world_fall"] : ["found", "admin.world_found"];
    const row = h(
      "li",
      { class: "row", "data-settlement": town.key, "data-state": town.state },
      h(
        "div",
        {},
        h("strong", {}, town.name),
        h("span", { class: "muted small" }, ` ${t("admin.world_town_of", { realm: names[town.realm_id] ?? "", tier: town.tier })} · ${t(`admin.world_state_${town.state}`)}`),
        supplies,
      ),
      h("div", { class: "inline" },
        h("button", { type: "button", "aria-label": t("admin.world_supplies_show_name", { name: town.name }), onclick: () => attempt(shell, problem, async () => {
          const { supplies: found } = await api.get(`/api/admin/settlements/${town.id}/supply`);
          replace(supplies, ...found.map((each) => supplyRow(town, each)));
        }) }, t("admin.world_supplies_show")),
        h("button", { type: "button", "aria-label": t(`${action[1]}_name`, { name: town.name }), onclick: () => change(async () => {
          const { settlement } = await api.post(`/api/admin/settlements/${town.id}/${action[0]}`);
          row.replaceWith(settlementRow(settlement));
          return settlement;
        }, (now) => t(now.state === "live" ? "admin.world_found_done" : "admin.world_fall_done", { name: now.name })) }, t(action[1])),
      ),
    );
    return row;
  }

  function supplyRow(town, supply) {
    const input = h("input", { type: "number", min: "0", max: "100", step: "1", value: String(supply.cut), "aria-label": t("admin.world_cut_name", { material: supply.name, name: town.name }) });
    const row = h(
      "li",
      { class: "row", "data-material": supply.material },
      h("span", {}, t("admin.world_supply", { material: supply.name, level: supply.level, capacity: supply.capacity, rate: supply.rate, drain: supply.drain, cut: supply.cut })),
      h("div", { class: "inline" }, input, h("button", { type: "button", "aria-label": t("admin.world_cut_set_name", { material: supply.name, name: town.name }), onclick: () => change(async () => {
        const { supply: now } = await api.post(`/api/admin/settlements/${town.id}/supply/${supply.material}/cut`, { cut: Number(input.value) });
        row.replaceWith(supplyRow(town, now));
        return now;
      }, (now) => t("admin.world_cut_done", { material: now.name, name: town.name, cut: now.cut })) }, t("admin.world_cut_set"))),
    );
    return row;
  }

  async function load() {
    const { realms: found, settlements } = await api.get("/api/admin/realms");
    names = Object.fromEntries(found.map((each) => [each.id, each.name]));
    replace(realms, ...found.map(realmRow));
    replace(towns, ...settlements.map(settlementRow));
    none.hidden = found.length + settlements.length > 0;
  }

  const element = h(
    "section",
    { id: "admin-world", "aria-labelledby": "admin-world-title" },
    h("h2", { id: "admin-world-title" }, t("admin.world")),
    h("div", { class: "card" }, h("p", { class: "hint" }, t("admin.world_hint")), problem, note, h("h3", {}, t("admin.world_realms")), realms, h("h3", {}, t("admin.world_settlements")), towns, none),
  );
  return { element, load };
}

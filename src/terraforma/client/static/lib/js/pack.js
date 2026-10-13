// The Pack panel: what a hero carries (gold and items), what they gave and received, and the forms that give gold or an item to another
// hero. Who may be given to is the game's call (its economy's trade policy; teammates by default), so the browser offers the player's own
// heroes and a hero's number, and the server's answer says no, in its own words, when the game refuses.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function packPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "pack-problem" });
  const note = h("p", { class: "note", role: "status", id: "pack-note" });
  const body = h("div", { id: "pack-body" });
  const root = h("div", { class: "panel pack", id: "panel-pack" }, h("h2", {}, t("panel.pack")), body);
  let view = null; // GET /api/heroes/{id}/inventory
  let trades = []; // GET /api/heroes/{id}/trades
  let loaded = null; // the hero the view is of
  let recipient = ""; // what the player chose to give to, kept across redraws: a hero's id, or "" for none yet
  let number = ""; // a hero's number typed in
  let amount = "1";

  const heroName = (id) => roster.hero(id)?.name ?? t("pack.hero_number", { id });

  /** The hero to give to: the number typed in wins over the list. */
  const target = () => {
    const typed = Number(number);
    if (number.trim() !== "" && Number.isInteger(typed) && typed >= 1) return typed;
    return Number(recipient) || null;
  };

  async function load() {
    const id = roster.heroId;
    if (id === null) {
      view = null;
      loaded = null;
      return draw();
    }
    [view, trades] = await Promise.all([api.get(`/api/heroes/${id}/inventory`), api.get(`/api/heroes/${id}/trades`)]);
    loaded = id;
    draw();
  }

  function refresh() {
    attempt(shell, problem, load);
  }

  /** A gift; the pack and the ledger are read again after, so what is shown is the server's. */
  function give(path, values, done) {
    note.textContent = "";
    const to = target();
    if (to === null) {
      problem.textContent = t("pack.choose");
      return;
    }
    attempt(shell, problem, async () => {
      await api.post(`/api/heroes/${loaded}/${path}`, { to_hero_id: to, ...values });
      await load();
      note.textContent = done(heroName(to));
    });
  }

  function recipients() {
    const others = roster.heroes.filter((hero) => hero.id !== loaded);
    const select = h(
      "select",
      { id: "pack-to", "aria-label": t("pack.to"), onchange: (event) => (recipient = event.target.value) },
      h("option", { value: "" }, t("pack.nobody")),
      others.map((hero) => {
        const team = roster.teamOf(hero.id);
        return h("option", { value: String(hero.id), selected: String(hero.id) === recipient }, team ? t("pack.hero_of", { hero: hero.name, team: team.name }) : hero.name);
      }),
    );
    const typed = h("input", {
      id: "pack-to-number",
      type: "number",
      min: "1",
      value: number,
      "aria-label": t("pack.to_number"),
      oninput: (event) => (number = event.target.value),
    });
    return h("div", { class: "card", id: "pack-recipient" }, h("h3", {}, t("pack.to")), h("div", { class: "inline" }, select, typed), h("p", { class: "muted small" }, t("pack.to_hint")));
  }

  function goldForm() {
    const qty = h("input", {
      id: "pack-gold-amount",
      type: "number",
      min: "1",
      value: amount,
      "aria-label": t("pack.gold_amount"),
      oninput: (event) => (amount = event.target.value),
    });
    return h(
      "div",
      { class: "inline", id: "pack-gold" },
      qty,
      h("button", { type: "button", id: "pack-give-gold", onclick: () => give("give-gold", { amount: Number(qty.value) }, (name) => t("pack.gave_gold", { amount: Number(qty.value), name })) }, t("pack.give_gold")),
    );
  }

  function stackRow(stack) {
    const qty = h("input", { type: "number", min: "1", max: String(stack.qty), value: "1", class: "qty", "aria-label": t("pack.item_qty", { name: stack.name }) });
    return h(
      "li",
      { class: "row", "data-item": stack.name },
      h("span", {}, h("strong", {}, stack.name), ` ×${stack.qty}`, stack.equipped_in.length ? h("span", { class: "muted small" }, ` ${t("pack.worn", { slots: stack.equipped_in.join(", ") })}`) : null),
      h(
        "div",
        { class: "inline" },
        qty,
        h("button", { type: "button", "aria-label": t("pack.give_item", { name: stack.name }), onclick: () => give("give-item", { position: stack.position, qty: Number(qty.value) }, (name) => t("pack.gave_item", { count: Number(qty.value), item: stack.name, name })) }, t("pack.give")),
      ),
    );
  }

  const itemName = (key) => view?.items.find((stack) => stack.item === key)?.name ?? key;

  function tradeLine(trade) {
    const what = trade.kind === "gold" ? t("pack.what_gold", { amount: trade.qty }) : t("pack.what_item", { count: trade.qty, item: itemName(trade.item) });
    return h("li", { class: "row", "data-trade": trade.id }, t(trade.direction === "gave" ? "pack.ledger_gave" : "pack.ledger_got", { what, name: trade.other }));
  }

  function draw() {
    if (view === null) {
      replace(body, h("p", { class: "muted", id: "pack-none" }, t("pack.no_hero")), problem);
      return;
    }
    const heroes = h(
      "select",
      { id: "pack-hero", "aria-label": t("pack.hero"), onchange: (event) => { recipient = ""; roster.selectHero(Number(event.target.value)); } },
      roster.heroes.map((hero) => h("option", { value: String(hero.id), selected: hero.id === loaded }, hero.name)),
    );
    replace(
      body,
      h("div", { class: "inline" }, heroes, h("button", { type: "button", onclick: refresh }, t("panel.refresh"))),
      h("p", { id: "pack-gold-total" }, t("pack.gold", { gold: view.gold })),
      view.items.length ? h("ul", { class: "rows", "aria-label": t("pack.items") }, view.items.map(stackRow)) : h("p", { class: "muted" }, t("pack.empty")),
      recipients(),
      h("div", { class: "card" }, h("h3", {}, t("pack.give_gold_title")), goldForm()),
      problem,
      note,
      h("h3", {}, t("pack.ledger")),
      trades.length ? h("ul", { class: "rows", id: "pack-ledger", "aria-label": t("pack.ledger") }, trades.map(tradeLine)) : h("p", { class: "muted", id: "pack-ledger-none" }, t("pack.ledger_none")),
    );
  }

  // A change of hero (here or in Nearby) shows that hero's pack; a hero who was removed, or gold from a fight, is read again when the panel opens.
  roster.onChange(() => {
    if (!root.hidden && roster.heroId !== loaded) refresh();
  });
  draw();
  return { element: root, show: refresh };
}

// The Pack panel: what a hero carries (gold and items), what they gave and received, and the forms that give gold or an item to another
// hero. Who may be given to is the game's call (its economy's trade policy; teammates by default), so the browser offers the player's own
// heroes and a hero's number, and the server's answer says no, in its own words, when the game refuses. When the game keeps storage
// (Game(storage=...)) the panel also shows it, with a Store button on each stack and Take and Use on the stored ones; a game with none shows nothing.
// Each stack can be worn or wielded (and taken off), used on one of the player's heroes outside a fight, or thrown away; what the stack allows
// (`wearable`, `sided`, `usable`, and `refused` when the game's rules bar this hero from wearing it) comes from the server, which decides each call.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";
import { storageCard } from "./storage.js";

export function packPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "pack-problem" });
  const note = h("p", { class: "note", role: "status", id: "pack-note" });
  const body = h("div", { id: "pack-body" });
  const root = h("div", { class: "panel pack", id: "panel-pack" }, h("h2", {}, t("panel.pack")), body);
  let view = null; // GET /api/heroes/{id}/inventory
  let trades = []; // GET /api/heroes/{id}/trades
  let stored = { enabled: false, items: [] }; // GET /api/heroes/{id}/storage
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
    [view, trades, stored] = await Promise.all([api.get(`/api/heroes/${id}/inventory`), api.get(`/api/heroes/${id}/trades`), api.get(`/api/heroes/${id}/storage`)]);
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

  /** A move to or from storage (or a use from it); the pack and storage are read again after, so what is shown is the server's. */
  function keep(path, values, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      await api.post(`/api/heroes/${loaded}/storage/${path}`, values);
      await load();
      note.textContent = done;
    });
  }

  /** Wearing, taking off, using and throwing away; the pack is read again after, so what is shown is the server's. */
  function handle(path, values, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const answer = await api.post(`/api/heroes/${loaded}/${path}`, values);
      await load();
      note.textContent = done(answer);
    });
  }

  /** What a use changed: each resource that moved, as "HP 10 → 25". */
  const changes = (answer) =>
    Object.keys(answer.after)
      .filter((name) => answer.after[name] !== answer.before[name])
      .map((name) => `${name} ${answer.before[name]} → ${answer.after[name]}`)
      .join(", ");

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

  /** The buttons for what the hero can do with the stack itself. */
  function ownUse(stack, qty) {
    const buttons = [];
    if (stack.equipped_in.length) {
      buttons.push(h("button", { type: "button", "aria-label": t("pack.take_off_item", { name: stack.name }), onclick: () => handle("unequip", { position: stack.position }, () => t("pack.took_off", { name: stack.name })) }, t("pack.take_off")));
    } else if (stack.wearable) {
      const side = stack.sided ? h("select", { "aria-label": t("pack.side", { name: stack.name }) }, h("option", { value: "0" }, t("pack.left")), h("option", { value: "1" }, t("pack.right"))) : null;
      buttons.push(
        side,
        h("button", { type: "button", disabled: Boolean(stack.refused), title: stack.refused ?? "", "aria-label": t("pack.wear_item", { name: stack.name }), onclick: () => handle("equip", { position: stack.position, side: Number(side?.value ?? 0) }, (answer) => t("pack.wore", { name: stack.name, slots: answer.slots.join(", ") })) }, t("pack.wear")),
      );
    }
    if (stack.usable) {
      const on = h("select", { "aria-label": t("pack.use_on", { name: stack.name }) }, roster.heroes.map((hero) => h("option", { value: String(hero.id), selected: hero.id === loaded }, hero.name)));
      buttons.push(
        on,
        h("button", { type: "button", "aria-label": t("pack.use_item", { name: stack.name }), onclick: () => handle("use-item", { position: stack.position, target_hero_id: Number(on.value) }, (answer) => t("pack.used", { item: stack.name, name: heroName(answer.hero_id), change: changes(answer) })) }, t("pack.use")),
      );
    }
    buttons.push(h("button", { type: "button", "aria-label": t("pack.discard_item", { name: stack.name }), onclick: () => handle("inventory/discard", { position: stack.position, qty: Number(qty.value) }, (answer) => t("pack.discarded", { count: answer.discarded, item: stack.name })) }, t("pack.discard")));
    return buttons;
  }

  function stackRow(stack) {
    const qty = h("input", { type: "number", min: "1", max: String(stack.qty), value: "1", class: "qty", "aria-label": t("pack.item_qty", { name: stack.name }) });
    return h(
      "li",
      { class: "row", "data-item": stack.name },
      h("span", {}, h("strong", {}, stack.name), ` ×${stack.qty}`, stack.equipped_in.length ? h("span", { class: "muted small" }, ` ${t("pack.worn", { slots: stack.equipped_in.join(", ") })}`) : null, stack.refused ? h("span", { class: "muted small" }, ` ${stack.refused}`) : null),
      h("div", { class: "inline" }, ownUse(stack, qty)),
      h(
        "div",
        { class: "inline" },
        qty,
        h("button", { type: "button", "aria-label": t("pack.give_item", { name: stack.name }), onclick: () => give("give-item", { position: stack.position, qty: Number(qty.value) }, (name) => t("pack.gave_item", { count: Number(qty.value), item: stack.name, name })) }, t("pack.give")),
        stored.enabled
          ? h("button", { type: "button", "aria-label": t("storage.store_item", { name: stack.name }), onclick: () => keep("put", { position: stack.position, qty: Number(qty.value) }, t("storage.stored", { count: Number(qty.value), item: stack.name })) }, t("storage.store"))
          : null,
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
      storageCard(shell, stored, loaded, keep),
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

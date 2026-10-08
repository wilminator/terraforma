// The shop activity: the wares with prices, what the hero could sell and for how much, and their gold. The browser never names a
// price: it sends what to buy or sell and how many, and the server charges what the NPC's dialog says.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function shopActivity({ shell, heroId, finish, problem }) {
  const t = (key, values) => shell.text.get(key, values);
  const root = h("div", { class: "shop", id: "shop" });
  const note = h("p", { class: "note", role: "status", id: "shop-note" });
  const shop = `/api/heroes/${heroId}/dialog/shop`;

  async function load() {
    const view = await api.get(shop);
    draw(view);
  }

  const quantity = (label, max) => h("input", { type: "number", min: "1", max: String(max), value: "1", "aria-label": label, class: "qty" });

  function ware(item) {
    const qty = quantity(t("shop.qty_label", { name: item.name }), 99);
    return h(
      "li",
      { class: "row", "data-ware": item.name },
      h("span", {}, h("strong", {}, item.name), ` ${t("shop.price", { price: item.price })}`),
      h("div", { class: "inline" }, qty, h("button", { type: "button", "aria-label": t("shop.buy_label", { name: item.name }), onclick: () => trade("buy", { item: item.item, qty: Number(qty.value) }) }, t("shop.buy"))),
    );
  }

  function sellable(stack) {
    const qty = quantity(t("shop.sell_qty_label", { name: stack.name }), stack.qty);
    return h(
      "li",
      { class: "row", "data-sell": stack.name },
      h("span", {}, h("strong", {}, stack.name), ` ×${stack.qty} · ${t("shop.pays", { each: stack.each })}`),
      h("div", { class: "inline" }, qty, h("button", { type: "button", "aria-label": t("shop.sell_label", { name: stack.name }), onclick: () => trade("sell", { position: stack.position, qty: Number(qty.value) }) }, t("shop.sell"))),
    );
  }

  function draw(view) {
    replace(
      root,
      h("p", { id: "shop-gold" }, t("shop.gold", { gold: view.gold })),
      view.wares.length ? h("ul", { class: "rows", "aria-label": t("shop.wares") }, view.wares.map(ware)) : h("p", { class: "muted" }, t("shop.no_wares")),
      view.sellable.length ? h("h3", {}, t("shop.sellable")) : null,
      view.sellable.length ? h("ul", { class: "rows", "aria-label": t("shop.sellable") }, view.sellable.map(sellable)) : null,
      note,
      h("button", { type: "button", class: "primary", id: "shop-done", onclick: () => attempt(shell, problem, finish) }, t("shop.done")),
    );
  }

  /** A purchase or a sale; the shop is read again after, so the gold and the stock shown are the server's. */
  function trade(how, body) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const answer = await api.post(`${shop}/${how}`, body);
      await load();
      note.textContent = how === "buy" ? t("shop.bought", { count: answer.bought, cost: answer.cost }) : t("shop.sold", { count: answer.sold, gain: answer.paid });
    });
  }

  attempt(shell, problem, load);
  return root;
}

// The Market panel: the open listings of the guilds' marketplaces (fixed prices and auctions), buying and bidding, taking back the
// team's own listing, and the team's parcels (what was bought, won or paid in barter) to collect once they have arrived. The browser never
// names a price: the server charges what the listing asks and what the game's fee rule says, and says no in its own words. Posting a
// listing and paying by barter are not here yet.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function marketPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "market-problem" });
  const note = h("p", { class: "note", role: "status", id: "market-note" });
  const body = h("div", { id: "market-body" });
  const root = h("div", { class: "panel market", id: "panel-market" }, h("h2", {}, t("panel.market")), body);
  let rows = []; // GET /api/market/listings
  let parcels = []; // GET /api/heroes/{id}/parcels
  let loaded = null; // the hero the view is of
  const filter = { text: "", guild: "", gold: "" };
  let deliverTo = ""; // where bought goods go; empty: the listing's own guild
  const bids = new Map(); // listing id -> the amount typed in

  const goods = (pairs) => pairs.map(([item, qty]) => t("market.good", { count: qty, item })).join(", ");

  async function load() {
    const id = roster.heroId;
    loaded = id;
    const query = new URLSearchParams();
    if (filter.text.trim()) query.set("text", filter.text.trim());
    if (filter.guild.trim()) query.set("guild", filter.guild.trim());
    if (filter.gold.trim()) query.set("max_gold", filter.gold.trim());
    const search = query.size ? `?${query}` : "";
    [rows, parcels] = await Promise.all([api.get(`/api/market/listings${search}`), id === null ? [] : api.get(`/api/heroes/${id}/parcels`)]);
    draw();
  }

  function refresh() {
    attempt(shell, problem, load);
  }

  /** A change; the listings and parcels are read again after, so what is shown is the server's. */
  function act(path, values, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      await api.post(`/api/heroes/${loaded}/${path}`, values);
      await load();
      note.textContent = done;
    });
  }

  const mine = (row) => roster.teamOf(loaded)?.name === row.seller;

  function price(row) {
    const parts = [];
    if (row.kind === "auction") parts.push(row.bid_gold ? t("market.bid_now", { gold: row.bid_gold }) : t("market.start", { gold: row.price_gold }));
    else {
      if (row.price_gold) parts.push(t("market.gold", { gold: row.price_gold }));
      if (row.price_tokens) parts.push(t("market.tokens", { tokens: row.price_tokens }));
      if (row.asks.length) parts.push(t("market.wants", { items: goods(row.asks) }));
    }
    return parts.join(" + ");
  }

  function listing(row) {
    const info = h(
      "span",
      {},
      h("strong", {}, goods(row.goods)),
      ` ${t("market.at", { guild: row.guild, seller: row.seller })} · ${price(row)}`,
    );
    const where = deliverTo.trim() || row.guild;
    let actions;
    if (loaded === null) actions = null;
    else if (mine(row)) {
      actions = h("button", { type: "button", "aria-label": t("market.take_back_listing", { id: row.id }), onclick: () => act(`listings/${row.id}/cancel`, {}, t("market.taken_back")) }, t("market.take_back"));
    } else if (row.kind === "auction") {
      const amount = h("input", {
        type: "number", min: "1", class: "qty", value: bids.get(row.id) ?? String(row.bid_gold ? row.bid_gold + 1 : row.price_gold),
        "aria-label": t("market.bid_amount", { id: row.id }), oninput: (event) => bids.set(row.id, event.target.value),
      });
      actions = h("div", { class: "inline" }, amount, h("button", { type: "button", "aria-label": t("market.bid_on", { id: row.id }), onclick: () => act(`listings/${row.id}/bid`, { amount: Number(amount.value), to_guild: where }, t("market.bid_placed")) }, t("market.bid")));
    } else if (row.asks.length) {
      actions = h("span", { class: "muted small" }, t("market.barter_later"));
    } else {
      actions = h("button", { type: "button", "aria-label": t("market.buy_listing", { id: row.id }), onclick: () => act(`listings/${row.id}/buy`, { to_guild: where }, t("market.bought")) }, t("market.buy"));
    }
    return h("li", { class: "row", "data-listing": row.id }, info, actions);
  }

  function parcel(each) {
    const contents = [each.gold ? t("market.gold", { gold: each.gold }) : null, each.items.length ? goods(each.items) : null].filter(Boolean).join(", ");
    return h(
      "li",
      { class: "row", "data-parcel": each.id, "data-state": each.state },
      h("span", {}, h("strong", {}, contents || t("market.nothing")), ` ${t("market.parcel_at", { guild: each.guild, when: new Date(each.arrives_at * 1000).toLocaleString() })}`),
      each.state === "collected" || loaded === null
        ? null
        : h("button", { type: "button", "aria-label": t("market.collect_parcel", { id: each.id }), onclick: () => act(`parcels/${each.id}/collect`, {}, t("market.collected")) }, t("market.collect")),
    );
  }

  function search() {
    const field = (id, label, key, type = "text") =>
      h("input", { id, type, value: filter[key], "aria-label": label, placeholder: label, oninput: (event) => (filter[key] = event.target.value) });
    return h(
      "form",
      { class: "inline", id: "market-search", onsubmit: (event) => { event.preventDefault(); refresh(); } },
      field("market-text", t("market.text"), "text"),
      field("market-guild", t("market.guild"), "guild"),
      field("market-gold", t("market.max_gold"), "gold", "number"),
      h("button", { type: "submit" }, t("market.search")),
    );
  }

  function draw() {
    const deliver = h("input", {
      id: "market-deliver", type: "text", value: deliverTo, "aria-label": t("market.deliver_to"), placeholder: t("market.deliver_to"),
      oninput: (event) => (deliverTo = event.target.value),
    });
    replace(
      body,
      search(),
      h("div", { class: "inline" }, deliver, h("span", { class: "muted small" }, t("market.deliver_hint"))),
      rows.length ? h("ul", { class: "rows", id: "market-rows", "aria-label": t("market.listings") }, rows.map(listing)) : h("p", { class: "muted", id: "market-none" }, t("market.none")),
      problem,
      note,
      h("h3", {}, t("market.parcels")),
      loaded === null
        ? h("p", { class: "muted", id: "market-no-hero" }, t("market.no_hero"))
        : parcels.length ? h("ul", { class: "rows", id: "market-parcels", "aria-label": t("market.parcels") }, parcels.map(parcel)) : h("p", { class: "muted", id: "market-parcels-none" }, t("market.parcels_none")),
    );
  }

  roster.onChange(() => {
    if (!root.hidden && roster.heroId !== loaded) refresh();
  });
  draw();
  return { element: root, show: refresh };
}

// The market activity: a guild's marketplace, reached from the NPC's `market,<guild>` tag. It searches the open listings (at every guild,
// or only this one), buys one for delivery to this guild, bids on an auction, posts goods from the hero's pack for sale here, and collects
// the parcels that have come to this guild. The browser never works out a price or a fee: it sends what to do, and the server's answer
// (a refusal in its own words, or the parcel) is what is shown. Goods are named by their item keys, as the listings carry them.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function marketActivity({ shell, heroId, prompt, finish, problem }) {
  const t = (key, values) => shell.text.get(key, values);
  const guild = prompt.parts[0];
  const root = h("div", { class: "market", id: "market" });
  const note = h("p", { class: "note", role: "status", id: "market-note" });
  let everywhere = false; // search every guild, not only this one
  let text = "";
  let maxGold = "";
  let found = [];
  let parcels = [];
  let pack = { gold: 0, items: [] };

  const goods = (rows) => rows.map(([key, qty]) => `${key} ×${qty}`).join(", ");

  function price(listing) {
    const parts = [];
    if (listing.price_gold) parts.push(t("market.gold", { gold: listing.price_gold }));
    if (listing.price_tokens) parts.push(t("market.tokens", { tokens: listing.price_tokens }));
    if (listing.asks.length) parts.push(t("market.asks", { items: goods(listing.asks) }));
    return parts.join(" + ") || t("market.free");
  }

  async function load() {
    const query = new URLSearchParams();
    if (!everywhere) query.set("guild", guild);
    if (text.trim()) query.set("text", text.trim());
    if (maxGold.trim() !== "") query.set("max_gold", maxGold.trim());
    let all;
    [found, all, pack] = await Promise.all([
      api.get(`/api/market/listings?${query}`),
      api.get(`/api/heroes/${heroId}/parcels`),
      api.get(`/api/heroes/${heroId}/inventory`),
    ]);
    parcels = all.filter((parcel) => parcel.guild === guild);
    draw();
  }

  /** A change; everything is read again after, so what is shown is the server's. */
  function act(path, body, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const answer = await api.post(`/api/heroes/${heroId}/${path}`, body);
      await load();
      note.textContent = done(answer);
    });
  }

  function listingRow(listing) {
    const auction = listing.kind === "auction";
    let action = null;
    if (auction) {
      const bid = h("input", { type: "number", min: "1", value: String(Math.max(1, listing.bid_gold + 1)), class: "qty", "aria-label": t("market.bid_amount", { id: listing.id }) });
      action = h(
        "div",
        { class: "inline" },
        bid,
        h("button", { type: "button", "aria-label": t("market.bid_label", { id: listing.id }), onclick: () => act(`listings/${listing.id}/bid`, { amount: Number(bid.value), to_guild: guild }, () => t("market.bid_placed", { amount: Number(bid.value) })) }, t("market.bid")),
      );
    } else if (listing.asks.length) {
      action = h("span", { class: "muted small" }, t("market.barter_note"));
    } else {
      action = h("button", { type: "button", "aria-label": t("market.buy_label", { id: listing.id }), onclick: () => act(`listings/${listing.id}/buy`, { to_guild: guild }, () => t("market.bought", { guild })) }, t("market.buy"));
    }
    return h(
      "li",
      { class: "row", "data-listing": String(listing.id) },
      h("span", {}, h("strong", {}, goods(listing.goods)), ` · ${auction ? t("market.auction_at", { gold: listing.bid_gold || listing.price_gold }) : price(listing)} · ${t("market.seller", { seller: listing.seller, guild: listing.guild })}`),
      action,
    );
  }

  function parcelRow(parcel) {
    const waiting = parcel.arrives_at > Math.floor(Date.now() / 1000);
    return h(
      "li",
      { class: "row", "data-parcel": String(parcel.id) },
      h("span", {}, [parcel.gold ? t("market.gold", { gold: parcel.gold }) : "", goods(parcel.items)].filter(Boolean).join(", ")),
      waiting
        ? h("span", { class: "muted small" }, t("market.on_the_way"))
        : h("button", { type: "button", "aria-label": t("market.collect_label", { id: parcel.id }), onclick: () => act(`parcels/${parcel.id}/collect`, {}, () => t("market.collected")) }, t("market.collect")),
    );
  }

  function postForm() {
    const stack = h("select", { id: "market-stack", "aria-label": t("market.stack") }, pack.items.map((item) => h("option", { value: String(item.position) }, `${item.name} ×${item.qty}`)));
    const qty = h("input", { type: "number", min: "1", value: "1", class: "qty", id: "market-qty", "aria-label": t("market.post_qty") });
    const gold = h("input", { type: "number", min: "0", value: "1", class: "qty", id: "market-price", "aria-label": t("market.post_price") });
    return h(
      "div",
      { class: "inline", id: "market-post" },
      stack,
      qty,
      gold,
      h("button", { type: "button", id: "market-post-button", onclick: () => act("listings", { guild, items: [{ position: Number(stack.value), qty: Number(qty.value) }], price_gold: Number(gold.value) }, () => t("market.posted", { guild })) }, t("market.post")),
    );
  }

  function draw() {
    const text_ = h("input", { type: "search", id: "market-text", value: text, "aria-label": t("market.search"), oninput: (event) => (text = event.target.value) });
    const limit = h("input", { type: "number", min: "0", id: "market-max", class: "qty", value: maxGold, "aria-label": t("market.max_gold"), oninput: (event) => (maxGold = event.target.value) });
    const wide = h("input", { type: "checkbox", id: "market-everywhere", checked: everywhere, onchange: (event) => (everywhere = event.target.checked) });
    replace(
      root,
      h("h3", {}, t("market.title", { guild })),
      h("p", { id: "market-gold" }, t("market.your_gold", { gold: pack.gold })),
      h(
        "form",
        { class: "inline", id: "market-search", onsubmit: (event) => (event.preventDefault(), attempt(shell, problem, load)) },
        text_,
        limit,
        h("label", {}, wide, ` ${t("market.everywhere")}`),
        h("button", { type: "submit" }, t("market.find")),
      ),
      found.length ? h("ul", { class: "rows", "aria-label": t("market.listings") }, found.map(listingRow)) : h("p", { class: "muted", id: "market-none" }, t("market.none")),
      h("h3", {}, t("market.parcels")),
      parcels.length ? h("ul", { class: "rows", "aria-label": t("market.parcels") }, parcels.map(parcelRow)) : h("p", { class: "muted", id: "market-no-parcels" }, t("market.no_parcels")),
      h("h3", {}, t("market.sell")),
      pack.items.length ? postForm() : h("p", { class: "muted" }, t("market.nothing_to_sell")),
      note,
      h("button", { type: "button", class: "primary", id: "market-done", onclick: () => attempt(shell, problem, finish) }, t("market.done")),
    );
  }

  attempt(shell, problem, load);
  return root;
}

// The storage part of the Pack panel: what the hero's team (or account) keeps beside the packs, as the game's storage rule allows.
// A game with no storage answers { enabled: false } and nothing is drawn. The moves are made by the Pack panel, which reads the
// pack and storage again after each, so what is shown is the server's; a refusal from the game's rules is shown in its own words.

import { h } from "./dom.js";

const OWNERS = ["team", "account"]; // (a game may add kinds of its own: they are shown as "storage")

/** The card for $stored (GET /api/heroes/{id}/storage), or null. $keep(path, values, note) makes a move for $heroId. */
export function storageCard(shell, stored, heroId, keep) {
  if (!stored.enabled || heroId === null) return null;
  const t = (key, values) => shell.text.get(key, values);

  function row(stack) {
    const qty = h("input", { type: "number", min: "1", max: String(stack.qty), value: "1", class: "qty", "aria-label": t("storage.item_qty", { name: stack.name }) });
    return h(
      "li",
      { class: "row", "data-stored": stack.name },
      h("span", {}, h("strong", {}, stack.name), ` ×${stack.qty}`),
      h(
        "div",
        { class: "inline" },
        qty,
        h("button", { type: "button", "aria-label": t("storage.take_item", { name: stack.name }), onclick: () => keep("take", { position: stack.position, qty: Number(qty.value) }, t("storage.taken", { count: Number(qty.value), item: stack.name })) }, t("storage.take")),
        h("button", { type: "button", "aria-label": t("storage.use_item", { name: stack.name }), onclick: () => keep("use", { position: stack.position }, t("storage.used", { item: stack.name })) }, t("storage.use")),
      ),
    );
  }

  return h(
    "div",
    { class: "card", id: "pack-storage" },
    h("h3", {}, t("storage.title")),
    h("p", { class: "muted small", id: "pack-storage-count" }, t("storage.count", { used: stored.items.length, capacity: stored.capacity, owner: t(OWNERS.includes(stored.owner) ? `storage.owner_${stored.owner}` : "storage.owner_other") })),
    stored.items.length ? h("ul", { class: "rows", "aria-label": t("storage.title") }, stored.items.map(row)) : h("p", { class: "muted", id: "pack-storage-none" }, t("storage.empty")),
  );
}

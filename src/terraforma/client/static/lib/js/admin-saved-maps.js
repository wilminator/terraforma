// The maps a game generated and kept for reuse (maps/library_routes.py): an admin looks them over, approves one so it is copied for later places,
// turns one down, or drops one (maps already made from it stay).

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function savedMapsSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "saved-maps-problem" });
  const note = h("p", { class: "note", role: "status", id: "saved-maps-note" });
  const list = h("ul", { class: "rows", id: "saved-maps-list", "aria-label": t("admin.saved_maps") });
  const none = h("p", { class: "muted", id: "saved-maps-none", hidden: true }, t("admin.saved_maps_none"));

  /** A decision on one saved map; the row shows the server's answer (or goes, for a dropped map). */
  function change(saved, action, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const { saved_map: now } = await api.post(`/api/admin/saved-maps/${saved.id}/${action}`);
      const current = list.querySelector(`[data-saved-map="${saved.id}"]`);
      if (action === "forget") current.remove();
      else current.replaceWith(row(now));
      none.hidden = list.children.length > 0;
      note.textContent = t(done, { key: now.key });
    });
  }

  function row(saved) {
    const actions = {
      proposed: [["approve", "admin.saved_map_approve", "admin.saved_map_approved"], ["reject", "admin.saved_map_reject", "admin.saved_map_rejected"]],
    }[saved.status] ?? [];
    actions.push(["forget", "admin.saved_map_forget", "admin.saved_map_forgotten"]);
    return h(
      "li",
      { class: "row", "data-saved-map": saved.id, "data-key": saved.key, "data-status": saved.status },
      h(
        "div",
        {},
        h("strong", {}, saved.key),
        h("span", { class: "muted small" }, ` ${t(`admin.saved_map_${saved.status}_state`)} · ${t("admin.saved_map_facts", { title: saved.title, width: saved.width, height: saved.height, objects: saved.objects, uses: saved.uses })}`),
      ),
      h("div", { class: "inline" }, actions.map(([action, label, done]) => h("button", { type: "button", "aria-label": t(`${label}_name`, { key: saved.key }), onclick: () => change(saved, action, done) }, t(label)))),
    );
  }

  async function load() {
    const { saved_maps: found } = await api.get("/api/admin/saved-maps");
    replace(list, ...found.map(row));
    none.hidden = found.length > 0;
  }

  const element = h(
    "section",
    { id: "admin-saved-maps", "aria-labelledby": "admin-saved-maps-title" },
    h("h2", { id: "admin-saved-maps-title" }, t("admin.saved_maps")),
    h("div", { class: "card" }, h("p", { class: "hint" }, t("admin.saved_maps_hint")), problem, note, list, none),
  );
  return { element, load };
}

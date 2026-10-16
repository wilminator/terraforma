// Ending a rotating quest slot's quest at once, because the world changed under it (quests/rotation_routes.py). The engine has no list of the
// game's slots, so the admin types the slot's key; teams that held the quest are done with it, nobody is penalized and the next ask starts a new period.

import * as api from "./api.js";
import { h } from "./dom.js";
import { confirmButton, field } from "./forms.js";

export function slotsSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "slots-problem" });
  const note = h("p", { class: "note", role: "status", id: "slots-note" });
  const slot = field("slot-key", t("admin.slot_key"), { maxlength: "64", autocomplete: "off" });
  const end = confirmButton(shell, problem, {
    id: "slot-end",
    label: t("admin.slot_end"),
    sure: t("admin.slot_end_sure"),
    run: async () => {
      note.textContent = "";
      const key = slot.input.value.trim();
      if (!key) {
        problem.textContent = t("admin.slot_missing");
        return;
      }
      const { ended } = await api.post(`/api/admin/quest-slots/${encodeURIComponent(key)}/end`);
      note.textContent = t(ended ? "admin.slot_ended" : "admin.slot_empty", { slot: key });
    },
  });
  const element = h("section", { id: "admin-slots", "aria-labelledby": "admin-slots-title" },
    h("h2", { id: "admin-slots-title" }, t("admin.slots")),
    h("div", { class: "card" }, h("p", { class: "hint" }, t("admin.slots_hint")), slot.row, end, problem, note));
  return { element, load: async () => {} };
}

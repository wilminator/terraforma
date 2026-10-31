// The planned and live state (gamestate/routes.py): an admin edits a section of the draft, checks it, commits it to make it live, or reverts it, and
// reads the committed snapshots. The sections are the game's own, so each is edited as JSON; the server checks it again and its words are shown as they
// are. An export file is imported here too (it replaces the draft, frozen, and the check says what is wrong with it).

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt, confirmButton, field } from "./forms.js";

const BASE = "/api/admin/state";

export function stateSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "state-problem" });
  const note = h("p", { class: "note", role: "status", id: "state-note" });
  const overview = h("div", { id: "state-overview" });
  const found = h("ul", { class: "rows", id: "state-found", "aria-label": t("admin.state_problems") });
  const history = h("ul", { class: "rows", id: "state-history", "aria-label": t("admin.state_history") });
  const picker = h("select", { id: "state-section", "aria-label": t("admin.state_pick"), onchange: () => show() });
  const value = h("textarea", { id: "state-value", rows: "14", spellcheck: "false", "aria-label": t("admin.state_value") });
  const commitNote = field("state-commit-note", t("admin.state_commit_note"), { maxlength: "200", autocomplete: "off" });
  let draft = null; // GET /planned: the revision, hash and the document

  const when = (iso) => new Date(iso).toLocaleString();

  /** The chosen section of the draft, as text to edit. */
  function show() {
    value.value = JSON.stringify(draft.document.sections[picker.value] ?? {}, null, 2);
  }

  async function load() {
    // One after another: the first look at a new database makes its first state, and calls that make it at the same moment each make one.
    const state = await api.get(BASE);
    const planned = await api.get(`${BASE}/planned`);
    const snapshots = await api.get(`${BASE}/history?limit=20`);
    draft = planned;
    const chosen = picker.value;
    const names = Object.keys(planned.document.sections);
    replace(picker, names.map((name) => h("option", { value: name, selected: name === chosen }, name)));
    show();
    replace(
      overview,
      h("p", { id: "state-live" }, t("admin.state_live", { id: state.live.id, by: state.live.committed_by ?? "-", when: when(state.live.committed_at) }), state.live.note ? ` ${state.live.note}` : ""),
      h("p", { id: "state-draft" }, t("admin.state_draft", { revision: state.planned.revision }), " ", t(state.planned.differs ? "admin.state_differs" : "admin.state_same"), state.planned.frozen ? ` ${t("admin.state_frozen")}` : ""),
    );
    replace(history, snapshots.snapshots.map((each) => h("li", { class: "row", "data-snapshot": each.id }, t("admin.state_snapshot", { id: each.id, by: each.committed_by ?? "-", when: when(each.committed_at) }), each.note ? ` ${each.note}` : "")));
  }

  const reload = () => attempt(shell, problem, load);

  /** What the check found, in the server's words (a good draft says so). */
  function listProblems(checked) {
    replace(found, checked.problems.map((each) => h("li", { class: "row problem", "data-section": each.section ?? "" }, each.section ? `${each.section}: ${each.message}` : each.message)));
  }

  const save = h("button", { type: "button", id: "state-save", onclick: () => {
    note.textContent = "";
    attempt(shell, problem, async () => {
      let parsed;
      try {
        parsed = JSON.parse(value.value);
      } catch {
        problem.textContent = t("admin.state_bad_json");
        return;
      }
      await api.put(`${BASE}/planned/sections/${encodeURIComponent(picker.value)}`, { value: parsed, revision: draft.revision });
      replace(found);
      await load();
      note.textContent = t("admin.state_saved", { section: picker.value });
    });
  } }, t("admin.state_save"));

  const check = h("button", { type: "button", id: "state-check", onclick: () => {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const checked = await api.post(`${BASE}/check`);
      listProblems(checked);
      note.textContent = t(checked.ok ? "admin.state_ok" : "admin.state_not_ok");
    });
  } }, t("admin.state_check"));

  /** Commits the draft the admin last read; a draft that does not check out is shown and not committed. */
  const commit = h("button", { type: "button", id: "state-commit", onclick: () => {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const checked = await api.post(`${BASE}/check`);
      listProblems(checked);
      if (!checked.ok) {
        note.textContent = t("admin.state_not_ok");
        return;
      }
      await api.post(`${BASE}/commit`, { hash: checked.hash, note: commitNote.input.value.trim() });
      commitNote.input.value = "";
      await load();
      note.textContent = t("admin.state_committed");
    });
  } }, t("admin.state_commit"));

  const revert = confirmButton(shell, problem, {
    id: "state-revert",
    label: t("admin.state_revert"),
    sure: t("admin.state_revert_sure"),
    run: async () => {
      note.textContent = "";
      await api.post(`${BASE}/revert`);
      replace(found);
      await load();
      note.textContent = t("admin.state_reverted");
    },
  });

  /** An export file picked from the admin's disk: its text goes to the server as it is (the server checks the file, the add-ons and every section). */
  const upload = h("input", { type: "file", id: "state-import", accept: "application/json,.json", "aria-label": t("admin.state_import"), onchange: (event) => {
    const [file] = event.target.files;
    event.target.value = ""; // (so the same file can be picked again)
    if (!file) return;
    note.textContent = "";
    attempt(shell, problem, async () => {
      const text = await file.text();
      try {
        JSON.parse(text);
      } catch {
        throw new api.ApiError(0, t("admin.state_import_bad"));
      }
      await api.post(`${BASE}/import`, new api.RawJson(text));  // (as it is: re-writing it would change numbers like 1.0, and so the hash)
      replace(found);
      await load();
      note.textContent = t("admin.state_imported");
    });
  } });

  const element = h(
    "section",
    { id: "admin-state", "aria-labelledby": "admin-state-title" },
    h("h2", { id: "admin-state-title" }, t("admin.state")),
    h("div", { class: "card" }, h("p", { class: "hint" }, t("admin.state_hint")), overview, h("button", { type: "button", onclick: reload }, t("panel.refresh"))),
    h(
      "div",
      { class: "card" },
      h("div", { class: "field" }, h("label", { for: "state-section" }, t("admin.state_pick")), picker),
      h("div", { class: "field" }, h("label", { for: "state-value" }, t("admin.state_value")), value),
      h("div", { class: "inline" }, save, check, revert),
    ),
    h("div", { class: "card" }, commitNote.row, h("div", { class: "inline" }, commit)),
    problem,
    note,
    found,
    h("div", { class: "card" }, h("h3", {}, t("admin.state_history")), history),
    h("p", { class: "inline" }, h("a", { href: `${BASE}/export`, id: "state-export-planned" }, t("admin.state_export_planned")), " ", h("a", { href: `${BASE}/export?source=live`, id: "state-export-live" }, t("admin.state_export_live"))),
    h("div", { class: "field" }, h("label", { for: "state-import" }, t("admin.state_import")), upload, h("p", { class: "hint" }, t("admin.state_import_hint"))),
  );
  return { element, load: reload };
}

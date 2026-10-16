// The admin's events: manual quests an admin writes as drafts, publishes, judges and cancels (quests/manual_routes.py). One section of the
// admin page: a list (narrowed by stage), one event with its entries (where a contest is judged), and the form that writes or rewrites a draft.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt, confirmButton, field, formCard } from "./forms.js";
import { area, formatObject, parseObject, parseWhole, showTime, toBox, toSeconds } from "./admin-fields.js";

const STAGES = ["all", "draft", "published", "cancelled"];

export function eventsSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "events-problem" });
  const holder = h("div", { class: "stack", id: "events" });
  let stage = "all";

  async function list() {
    const query = stage === "all" ? "" : `?status=${stage}`;
    const events = (await api.get(`/api/admin/manual-quests${query}`)).events;
    replace(
      holder,
      h("div", { class: "tabs", role: "group", "aria-label": t("admin.events_stage") }, STAGES.map((each) =>
        h("button", { type: "button", "aria-pressed": String(each === stage), onclick: () => attempt(shell, problem, () => { stage = each; return list(); }) }, t(`admin.stage_${each}`)))),
      h("p", {}, h("button", { type: "button", id: "event-new", class: "primary", onclick: () => form(null) }, t("admin.event_new"))),
      events.length
        ? h("ul", { class: "rows", id: "event-list", "aria-label": t("admin.events") }, events.map(row))
        : h("p", { class: "muted", id: "event-none" }, t("admin.events_none")),
    );
  }

  function row(event) {
    return h(
      "li",
      { class: "row", "data-event": event.key, "data-stage": event.status },
      h("div", { class: "row-main" },
        h("strong", {}, event.name), " ", h("span", { class: "muted small" }, t("admin.event_line", { key: event.key, status: t(`admin.stage_${event.status}`), entered: event.entered ?? 0 })),
        h("div", { class: "muted small" }, t("admin.event_window", { opens: showTime(event.opens), closes: showTime(event.closes), ends: showTime(event.ends) }))),
      h("div", { class: "row-actions" }, ...actions(event)),
    );
  }

  const stageChange = (event, verb) => () => attempt(shell, problem, async () => {
    await api.post(`/api/admin/manual-quests/${event.id}/${verb}`);
    await list();
  });

  function actions(event) {
    const open = h("button", { type: "button", "aria-label": t("admin.event_open_label", { name: event.name }), onclick: () => attempt(shell, problem, () => detail(event.id)) }, t("admin.event_open"));
    if (event.status === "draft") {
      return [
        open,
        h("button", { type: "button", "aria-label": t("admin.event_edit_label", { name: event.name }), onclick: () => form(event) }, t("admin.event_edit")),
        h("button", { type: "button", class: "primary", "aria-label": t("admin.event_publish_label", { name: event.name }), onclick: stageChange(event, "publish") }, t("admin.event_publish")),
      ];
    }
    if (event.status === "published") {
      return [open, confirmButton(shell, problem, { id: `event-cancel-${event.id}`, label: t("admin.event_cancel"), sure: t("admin.event_cancel_sure", { name: event.name }), run: stageChange(event, "cancel") })];
    }
    return [open];
  }

  async function detail(id) {
    const { event, entries } = await api.get(`/api/admin/manual-quests/${id}`);
    replace(
      holder,
      h("p", {}, h("button", { type: "button", id: "event-back", onclick: () => attempt(shell, problem, list) }, t("admin.back"))),
      h("section", { class: "card", id: "event-detail", "data-event": event.key },
        h("h2", {}, event.name),
        h("p", { class: "muted" }, t("admin.event_line", { key: event.key, status: t(`admin.stage_${event.status}`), entered: event.entered })),
        event.description ? h("p", { class: "bio" }, event.description) : null,
        h("p", { class: "small" }, t("admin.event_window", { opens: showTime(event.opens), closes: showTime(event.closes), ends: showTime(event.ends) })),
        h("p", { class: "small" }, t("admin.event_limits", { seconds: event.limit_seconds, max: event.max_entries })),
        event.map_id !== null ? h("p", { class: "small" }, t("admin.event_place", { map: event.map_id, x: event.x ?? "–", y: event.y ?? "–" })) : null),
      h("h3", {}, t("admin.entries")),
      entries.length ? h("ul", { class: "rows", id: "entry-list", "aria-label": t("admin.entries") }, entries.map((entry) => entryRow(event, entry))) : h("p", { class: "muted", id: "entry-none" }, t("admin.entries_none")),
    );
  }

  function entryRow(event, entry) {
    const score = field(`entry-score-${entry.id}`, t("admin.entry_score"), { inputmode: "numeric", autocomplete: "off" });
    const judge = (won) => () => attempt(shell, problem, async () => {
      await api.post(`/api/admin/manual-quests/${event.id}/entries/${entry.id}/result`, { won, score: parseWhole(score.input.value, t("admin.entry_score"), shell) });
      await detail(event.id);
    });
    return h(
      "li",
      { class: "row", "data-entry": entry.team, "data-status": entry.status },
      h("div", { class: "row-main" }, t("admin.entry_line", { team: entry.team ?? `#${entry.team_id}`, status: entry.status, score: entry.score ?? "–", started: showTime(entry.started) }), entry.failure ? h("span", { class: "muted small" }, ` (${entry.failure})`) : null),
      entry.status === "active"
        ? h("div", { class: "row-actions" }, score.row,
          h("button", { type: "button", class: "primary", "aria-label": t("admin.entry_won_label", { team: entry.team }), onclick: judge(true) }, t("admin.entry_won")),
          h("button", { type: "button", "aria-label": t("admin.entry_lost_label", { team: entry.team }), onclick: judge(false) }, t("admin.entry_lost")))
        : null,
    );
  }

  /** The form that writes a new draft ($event null) or rewrites one. */
  function form(event) {
    const key = field("event-key", t("admin.event_key"), { maxlength: "64", required: true, autocomplete: "off", value: event?.key ?? "" });
    const name = field("event-name", t("admin.event_name"), { maxlength: "64", required: true, autocomplete: "off", value: event?.name ?? "" });
    const kind = field("event-kind", t("admin.event_kind"), { maxlength: "32", autocomplete: "off", value: event?.kind ?? "" });
    const description = area("event-description", t("admin.event_description"), { maxlength: "4000" });
    description.input.value = event?.description ?? "";
    const opens = field("event-opens", t("admin.event_opens"), { type: "datetime-local", required: true, value: event ? toBox(event.opens) : "" });
    const closes = field("event-closes", t("admin.event_closes"), { type: "datetime-local", required: true, value: event ? toBox(event.closes) : "" });
    const ends = field("event-ends", t("admin.event_ends"), { type: "datetime-local", required: true, value: event ? toBox(event.ends) : "" });
    const limit = field("event-limit", t("admin.event_limit"), { inputmode: "numeric", autocomplete: "off", value: String(event?.limit_seconds ?? 0) });
    const max = field("event-max", t("admin.event_max"), { inputmode: "numeric", autocomplete: "off", value: String(event?.max_entries ?? 0) });
    const map = field("event-map", t("admin.event_map"), { inputmode: "numeric", autocomplete: "off", value: event?.map_id ?? "" });
    const x = field("event-x", t("admin.event_x"), { inputmode: "numeric", autocomplete: "off", value: event?.x ?? "" });
    const y = field("event-y", t("admin.event_y"), { inputmode: "numeric", autocomplete: "off", value: event?.y ?? "" });
    const spec = area("event-spec", t("admin.event_spec"), { rows: "6", spellcheck: "false" });
    spec.input.value = event ? formatObject(event.spec) : "";
    const card = formCard(shell, {
      id: "event-form",
      title: event ? "admin.event_edit_title" : "admin.event_new",
      rows: [key.row, name.row, kind.row, description.row, opens.row, closes.row, ends.row, limit.row, max.row, map.row, x.row, y.row, spec.row, h("p", { class: "hint" }, t("admin.event_hint"))],
      submit: "admin.event_save",
      run: async () => {
        const body = {
          key: key.input.value.trim(),
          name: name.input.value.trim(),
          kind: kind.input.value.trim(),
          description: description.input.value,
          spec: parseObject(spec.input.value, shell),
          opens_at: toSeconds(opens.input.value, t("admin.event_opens"), shell),
          closes_at: toSeconds(closes.input.value, t("admin.event_closes"), shell),
          ends_at: toSeconds(ends.input.value, t("admin.event_ends"), shell),
          limit_seconds: parseWhole(limit.input.value, t("admin.event_limit"), shell, 0),
          max_entries: parseWhole(max.input.value, t("admin.event_max"), shell, 0),
          map_id: parseWhole(map.input.value, t("admin.event_map"), shell),
          x: parseWhole(x.input.value, t("admin.event_x"), shell),
          y: parseWhole(y.input.value, t("admin.event_y"), shell),
        };
        if (event) await api.call("PATCH", `/api/admin/manual-quests/${event.id}`, body);
        else await api.post("/api/admin/manual-quests", body);
        await list();
      },
    });
    replace(holder, h("p", {}, h("button", { type: "button", id: "event-back", onclick: () => attempt(shell, problem, list) }, t("admin.back"))), card.form);
  }

  const element = h("section", { id: "admin-events", "aria-labelledby": "admin-events-title" }, h("h2", { id: "admin-events-title" }, t("admin.events")), holder, problem);
  return { element, load: () => attempt(shell, problem, list) };
}

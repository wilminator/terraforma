// The chat panel: the channels the player is in (a tab list), the lines said in the chosen one, a send box, a whisper box, a mute list
// and, for an admin, the flags waiting to be settled. Lines are asked for, not pushed: while the panel is open the page asks every few
// seconds for the lines after the last one it holds.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

const POLL_MS = 3000;

export function chatPanel(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const channelList = h("select", { id: "chat-channel", "aria-label": t("chat.channel"), onchange: () => choose() });
  const lines = h("ul", { class: "rows chat-lines", id: "chat-lines", "aria-label": t("chat.lines"), "aria-live": "polite" });
  const body = h("input", { id: "chat-body", name: "chat-body", maxlength: "500", autocomplete: "off", "aria-label": t("chat.say") });
  const whisper = h("input", { id: "chat-whisper", name: "chat-whisper", maxlength: "24", autocomplete: "off", "aria-label": t("chat.whisper_to") });
  const flagBar = h("div", { class: "card", id: "chat-flag", hidden: true });
  const muted = h("ul", { class: "rows", id: "chat-muted", "aria-label": t("chat.muted") });
  const admin = h("section", { id: "chat-admin", hidden: true });
  let channels = [];
  let current = null; // { kind, key }
  let last = 0;
  let timer = null;
  let reasons = [];

  const addressOf = (entry) => `${entry.kind}\n${entry.key}`;

  function labelOf(entry) {
    return entry.kind === "whisper" ? t("chat.whisper_label", { name: entry.label }) : entry.kind === "party" || entry.kind === "global" || entry.kind === "nearby" || entry.kind === "town" ? t(`chat.kind.${entry.kind}`) : entry.label;
  }

  function drawChannels() {
    const keep = current ? addressOf(current) : null;
    replace(channelList, channels.map((entry) => h("option", { value: addressOf(entry), selected: addressOf(entry) === keep }, labelOf(entry))));
    if (!channels.length) {
      current = null;
    } else if (!keep || !channels.some((entry) => addressOf(entry) === keep)) {
      const [kind, key] = channelList.value.split("\n");
      current = { kind, key };
      last = 0;
      replace(lines);
    }
  }

  function choose() {
    const [kind, key] = channelList.value.split("\n");
    current = { kind, key };
    last = 0;
    replace(lines);
    poll();
  }

  function lineElement(each) {
    const actions = each.mine
      ? null
      : h(
          "span",
          { class: "row-actions" },
          h("button", { type: "button", class: "small", "aria-label": t("chat.mute_label", { name: each.sender }), onclick: () => mute(each.sender) }, t("chat.mute")),
          h("button", { type: "button", class: "small", "aria-label": t("chat.flag_label", { name: each.sender }), onclick: () => startFlag(each) }, t("chat.flag")),
        );
    return h("li", { class: "row chat-line", "data-id": each.id, "data-mine": each.mine ? "yes" : "no" }, h("span", {}, h("strong", {}, each.sender), ": ", each.body), actions);
  }

  async function poll() {
    if (!current) return;
    const asked = current;
    await attempt(shell, problem, async () => {
      const answer = await api.get(`/api/chat/messages?kind=${encodeURIComponent(asked.kind)}&key=${encodeURIComponent(asked.key)}&after=${last}`);
      if (current !== asked) return; // the player moved on while it was answered
      for (const each of answer.messages) {
        lines.append(lineElement(each));
        last = Math.max(last, each.id);
      }
      if (answer.messages.length) lines.lastElementChild.scrollIntoView?.({ block: "nearest" });
    });
  }

  async function refreshChannels() {
    await attempt(shell, problem, async () => {
      const answer = await api.get("/api/chat/channels");
      channels = answer.channels;
      await drawAdmin(answer.admin);
      if (current?.kind === "whisper" && !channels.some((entry) => addressOf(entry) === addressOf(current))) {
        channels.push({ ...current, label: current.key });
      }
      drawChannels();
    });
    await poll();
  }

  async function send(event) {
    event.preventDefault();
    if (!current || !body.value.trim()) return;
    await attempt(shell, problem, async () => {
      await api.post("/api/chat/say", { kind: current.kind, key: current.key, body: body.value });
      body.value = "";
    });
    await poll();
  }

  async function startWhisper(event) {
    event.preventDefault();
    const handle = whisper.value.trim();
    if (!handle) return;
    const entry = { kind: "whisper", key: handle, label: handle };
    if (!channels.some((each) => each.kind === "whisper" && each.key === handle)) channels.push(entry);
    current = entry;
    last = 0;
    replace(lines);
    whisper.value = "";
    drawChannels();
    channelList.value = addressOf(entry);
    await poll();
  }

  async function drawMuted() {
    const handles = (await api.get("/api/chat/mutes")).muted;
    replace(
      muted,
      handles.length
        ? handles.map((name) => h("li", { class: "row", "data-handle": name }, h("span", {}, name), h("button", { type: "button", "aria-label": t("chat.unmute_label", { name }), onclick: () => unmute(name) }, t("chat.unmute"))))
        : h("li", { class: "muted" }, t("chat.nobody_muted")),
    );
  }

  async function mute(name) {
    await attempt(shell, problem, async () => {
      await api.post("/api/chat/mute", { handle: name });
      await drawMuted();
      replace(lines);
      last = 0;
    });
    await poll();
  }

  async function unmute(name) {
    await attempt(shell, problem, async () => {
      await api.post("/api/chat/unmute", { handle: name });
      await drawMuted();
      replace(lines);
      last = 0;
    });
    await poll();
  }

  function startFlag(each) {
    const reason = h("select", { id: "chat-flag-reason", "aria-label": t("chat.flag_reason") }, reasons.map((name) => h("option", { value: name }, t(`chat.reason.${name}`) === `chat.reason.${name}` ? name : t(`chat.reason.${name}`))));
    const note = h("input", { id: "chat-flag-note", maxlength: "280", "aria-label": t("chat.flag_note"), autocomplete: "off" });
    const said = h("p", { class: "muted small" }, t("chat.flagging", { name: each.sender, body: each.body }));
    replace(
      flagBar,
      said,
      h("div", { class: "field" }, reason),
      h("div", { class: "field" }, note),
      h(
        "div",
        { class: "row-actions" },
        h("button", { type: "button", id: "chat-flag-send", class: "primary", onclick: () => attempt(shell, problem, async () => {
          await api.post("/api/chat/flag", { message_id: each.id, reason: reason.value, note: note.value });
          flagBar.hidden = true;
          replace(flagBar);
          flagged.textContent = t("chat.flagged");
        }) }, t("chat.flag_send")),
        h("button", { type: "button", id: "chat-flag-cancel", onclick: () => { flagBar.hidden = true; replace(flagBar); } }, t("panel.cancel")),
      ),
    );
    flagBar.hidden = false;
  }
  const flagged = h("p", { class: "note", role: "status", id: "chat-flagged" });

  async function drawAdmin(isAdmin) {
    if (!isAdmin) {
      admin.hidden = true;
      return;
    }
    const flags = (await api.get("/api/chat/admin/flags")).flags;
    admin.hidden = false;
    replace(
      admin,
      h("h3", {}, t("chat.admin_title")),
      h(
        "ul",
        { class: "rows", id: "chat-admin-flags" },
        flags.length
          ? flags.map((each) =>
              h(
                "li",
                { class: "row", "data-flag": each.id },
                h("span", {}, t("chat.admin_flag", { reporter: each.reporter, sender: each.message.sender, body: each.message.body, reason: each.reason })),
                h(
                  "span",
                  { class: "row-actions" },
                  h("button", { type: "button", "aria-label": t("chat.admin_actioned_label", { id: each.id }), onclick: () => settle(each.id, "actioned") }, t("chat.admin_actioned")),
                  h("button", { type: "button", "aria-label": t("chat.admin_dismiss_label", { id: each.id }), onclick: () => settle(each.id, "dismissed") }, t("chat.admin_dismiss")),
                ),
              ),
            )
          : h("li", { class: "muted" }, t("chat.admin_none")),
      ),
    );
  }

  async function settle(id, state) {
    await attempt(shell, problem, async () => {
      await api.post("/api/chat/admin/settle", { flag_id: id, state });
      await drawAdmin(true);
    });
  }

  async function show() {
    flagged.textContent = "";
    await attempt(shell, problem, async () => {
      reasons = (await api.get("/api/chat/reasons")).reasons;
      await drawMuted();
    });
    await refreshChannels();
    clearInterval(timer);
    timer = setInterval(() => {
      if (element.hidden || !element.isConnected) return;
      poll();
    }, POLL_MS);
  }

  const element = h(
    "div",
    { class: "panel", id: "panel-chat" },
    h("h2", {}, t("panel.chat")),
    h("div", { class: "field" }, h("label", { for: "chat-channel" }, t("chat.channel")), channelList),
    lines,
    h("form", { class: "inline", onsubmit: send }, body, h("button", { type: "submit", class: "primary" }, t("chat.send"))),
    h("form", { class: "inline", onsubmit: startWhisper }, whisper, h("button", { type: "submit" }, t("chat.whisper"))),
    problem,
    flagged,
    flagBar,
    h("h3", {}, t("chat.muted")),
    muted,
    admin,
  );
  return { element, show };
}

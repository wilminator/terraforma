// The quests the engine made for gaps in the world's progression (quests/needs_routes.py): an admin looks them over, approves one to make it
// live in its slot, turns one down, retires a live one, and asks for the gap check now (it also runs from housekeeping). A quest's spec is the
// game's own, so it is shown as text and never edited here.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

const PAGE = 50;
const STAGES = ["proposed", "live", "rejected", "retired", "all"];

export function proposalsSection(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "proposals-problem" });
  const note = h("p", { class: "note", role: "status", id: "proposals-note" });
  const list = h("ul", { class: "rows", id: "proposals-list", "aria-label": t("admin.proposals") });
  const none = h("p", { class: "muted", id: "proposals-none", hidden: true }, t("admin.proposals_none"));
  const more = h("button", { type: "button", id: "proposals-more", hidden: true }, t("admin.proposals_more"));
  let stage = "proposed";
  let last = 0; // the highest id shown, for "More"

  const path = () => `/api/admin/quest-proposals?limit=${PAGE}&after=${last}${stage === "all" ? "" : `&status=${stage}`}`;

  /** One page more of the stage's proposals, oldest first. */
  async function page() {
    const { proposals } = await api.get(path());
    for (const each of proposals) list.append(row(each));
    if (proposals.length) last = Math.max(...proposals.map((each) => each.id));
    none.hidden = list.children.length > 0;
    more.hidden = proposals.length < PAGE;
  }

  async function reset() {
    last = 0;
    replace(list);
    await page();
  }

  /** A change to one proposal; the row shows the server's answer. */
  function change(proposal, action, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const { proposal: now } = await api.post(`/api/admin/quest-proposals/${proposal.id}/${action}`);
      const row_ = list.querySelector(`[data-proposal="${proposal.id}"]`);
      if (stage === "all") row_.replaceWith(row(now));
      else row_.remove();
      none.hidden = list.children.length > 0;
      note.textContent = t(done, { name: now.name });
    });
  }

  function row(proposal) {
    const actions = {
      proposed: [["approve", "admin.proposal_approve", "admin.proposal_approved"], ["reject", "admin.proposal_reject", "admin.proposal_rejected"]],
      live: [["retire", "admin.proposal_retire", "admin.proposal_retired"]],
    }[proposal.status] ?? [];
    return h(
      "li",
      { class: "row", "data-proposal": proposal.id, "data-status": proposal.status },
      h(
        "div",
        {},
        h("strong", {}, proposal.name),
        h("span", { class: "muted small" }, ` ${t("admin.proposal_where", { slot: proposal.slot, min: proposal.pxp_min, max: proposal.pxp_max })} · ${t(`admin.stage_${proposal.status}`)}`),
        h("p", { class: "muted small" }, t("admin.proposal_why", { band: proposal.band, reason: proposal.reason })),
        h("pre", { class: "small", "aria-label": t("admin.proposal_spec", { name: proposal.name }) }, JSON.stringify(proposal.spec)),
      ),
      h("div", { class: "inline" }, actions.map(([action, label, done]) => h("button", { type: "button", "aria-label": t(`${label}_name`, { name: proposal.name }), onclick: () => change(proposal, action, done) }, t(label)))),
    );
  }

  const pick = h(
    "select",
    { id: "proposals-stage", "aria-label": t("admin.proposals_stage"), onchange: (event) => { stage = event.target.value; attempt(shell, problem, reset); } },
    STAGES.map((each) => h("option", { value: each }, t(`admin.stage_${each}`))),
  );
  const run = h("button", { type: "button", id: "proposals-run", onclick: () => {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const { proposals } = await api.post("/api/admin/quest-needs/run");
      await reset();
      note.textContent = t("admin.proposals_ran", { count: proposals.length });
    });
  } }, t("admin.proposals_run"));
  more.onclick = () => attempt(shell, problem, page);

  const element = h(
    "section",
    { id: "admin-proposals", "aria-labelledby": "admin-proposals-title" },
    h("h2", { id: "admin-proposals-title" }, t("admin.proposals")),
    h("div", { class: "card" }, h("p", { class: "hint" }, t("admin.proposals_hint")), h("div", { class: "inline" }, pick, run), problem, note, list, none, more),
  );
  return { element, load: reset };
}

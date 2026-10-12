// The player's alliances: the ones their teams are in, the invitations waiting for their teams, and founding a new one.
// A regular page, for any screen and orientation; the account page links here.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt, field, formCard } from "./forms.js";
import { Shell } from "./shell.js";

const shell = new Shell("alliances");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);
const problem = h("p", { class: "problem", role: "alert", id: "alliances-problem" });
let teams = [];
let mine = { alliances: [], invitations: [] };

const hereIs = (alliance, team) => `/alliance/${alliance}?team=${team}`;

function invitationRow(invitation) {
  const answer = (verb, then) => () => attempt(shell, problem, async () => {
    const view = await api.post(`/api/teams/${invitation.team_id}/invitations/${verb}`, { alliance_id: invitation.alliance_id });
    if (then) location.assign(hereIs(view.id, invitation.team_id));
    else await reload();
  });
  return h(
    "li",
    { class: "row", "data-invitation": invitation.alliance, "data-team": invitation.team },
    h("div", { class: "row-main" }, t("alliances.invited", { alliance: invitation.alliance, team: invitation.team })),
    h("div", { class: "row-actions" },
      h("button", { type: "button", class: "primary", "aria-label": t("alliances.accept_label", invitation), onclick: answer("accept", true) }, t("alliances.accept")),
      h("button", { type: "button", "aria-label": t("alliances.decline_label", invitation), onclick: answer("decline", false) }, t("alliances.decline"))),
  );
}

function allianceRow(row) {
  return h(
    "li",
    { class: "row", "data-alliance": row.name, "data-team": row.team },
    h("div", { class: "row-main" }, h("a", { href: hereIs(row.id, row.team_id) }, row.name), " ", h("span", { class: "muted small" }, t("alliances.as", { team: row.team, role: row.role }))),
  );
}

function foundCard() {
  const team = h("select", { id: "found-team", name: "found-team" }, teams.map((each) => h("option", { value: String(each.id) }, each.name)));
  const name = field("found-name", t("alliances.name"), { maxlength: "80", required: true, autocomplete: "off" });
  const card = formCard(shell, {
    id: "found-form",
    title: "alliances.found",
    rows: [h("div", { class: "field" }, h("label", { for: "found-team" }, t("alliances.team")), team), name.row],
    submit: "alliances.found_submit",
    run: async () => {
      const made = await api.post("/api/alliances", { team_id: Number(team.value), name: name.input.value });
      location.assign(hereIs(made.id, Number(team.value)));
    },
  });
  return card.form;
}

function draw() {
  const hasTeam = teams.length > 0;
  replace(
    main,
    h(
      "div",
      { class: "stack" },
      h("h1", {}, t("alliances.title")),
      h("p", {}, h("a", { href: "/account/", id: "back-to-account" }, t("alliances.back"))),
      mine.invitations.length
        ? h("section", { class: "card", id: "invitations" }, h("h2", {}, t("alliances.invitations")), h("ul", { class: "rows", "aria-label": t("alliances.invitations") }, mine.invitations.map(invitationRow)))
        : null,
      mine.alliances.length
        ? h("section", { class: "card" }, h("ul", { class: "rows", "aria-label": t("alliances.list") }, mine.alliances.map(allianceRow)))
        : h("p", { class: "muted" }, t("alliances.none")),
      hasTeam ? foundCard() : h("p", {}, t("alliances.no_team"), " ", h("a", { href: "/play/" }, t("alliances.make_team"))),
      problem,
    ),
  );
}

async function reload() {
  mine = await api.get("/api/alliances");
  draw();
}

await shell.start();
document.title = `${t("alliances.title")} · ${shell.game.game ?? "TerraForma"}`;
if (!(await shell.restore())) {
  location.replace("/account/?next=alliances"); // one login; it sends the player back here
} else {
  await attempt(shell, problem, async () => {
    teams = await api.get("/api/teams");
    await reload();
  });
  if (!main.firstChild) replace(main, problem);
  if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

// One alliance, as one of the player's teams sees it: the teams in it with their roles, who is invited, the page and ballots, and
// how the alliance sees others. What a role may do is the game's rule: the page shows the controls the server says the role has,
// and the server still decides each call. A regular page, for any screen and orientation.

import * as api from "./api.js";
import { pageCard } from "./alliance-page.js";
import { ballotsCard } from "./ballots.js";
import { h, replace } from "./dom.js";
import { attempt, confirmButton, field, formCard } from "./forms.js";
import { relationsCard } from "./relationships.js";
import { Shell } from "./shell.js";

const shell = new Shell("alliance");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);
const id = Number(location.pathname.split("/").filter(Boolean).pop());
const problem = h("p", { class: "problem", role: "alert", id: "alliance-problem" });
const back = h("p", {}, h("a", { href: "/alliances/", id: "back-to-alliances" }, t("alliance.back")));

let homes = []; // this player's teams that are in the alliance: { team_id, team, role }
let view = null; // the alliance as the acting team sees it
let people = []; // the roster: every team, with a link to its page only while it is visible
let ctx = null;

const calls = (path) => `/api/alliances/${id}${path}`;

/** Runs $work (async); a refusal is shown under the page, in the server's words. Then the page is drawn again from the server. */
const act = (work) => attempt(shell, problem, async () => {
  await work();
  await redraw();
});

function memberRow(member) {
  const me = member.team_id === view.you.team_id;
  const can = ctx.can;
  const myRank = view.roles.indexOf(view.you.role);
  const roles = view.roles.filter((role, index) => index > myRank || role === member.role);
  const name = member.page ? h("a", { href: `/p/team/${member.page}` }, member.name) : member.name;
  const handle = member.handle ? (member.player ? h("a", { href: `/p/${member.player}` }, member.handle) : member.handle) : null;
  const choose = !me && can.has("set_role")
    ? h(
        "select",
        {
          "aria-label": t("alliance.role_of", { name: member.name }),
          onchange: (event) => act(() => api.post(calls("/role"), { team_id: ctx.teamId, target_team_id: member.team_id, role: event.target.value })),
        },
        roles.map((role) => h("option", { value: role, selected: role === member.role }, role)),
      )
    : h("span", { class: "muted small" }, member.role);
  return h(
    "li",
    { class: "row", "data-team": member.name },
    h("div", { class: "row-main" }, h("strong", {}, name), " ", choose, " ", handle, member.page ? null : h("span", { class: "muted small" }, ` ${t("alliance.hidden")}`)),
    me
      ? null
      : h(
          "div",
          { class: "row-actions" },
          can.has("hand_over")
            ? h("button", { type: "button", "aria-label": t("alliance.hand_over_label", { name: member.name }), onclick: () => act(() => api.post(calls("/hand-over"), { team_id: ctx.teamId, target_team_id: member.team_id })) }, t("alliance.hand_over"))
            : null,
          can.has("remove")
            ? h("button", { type: "button", class: "danger", "aria-label": t("alliance.remove_label", { name: member.name }), onclick: () => act(() => api.post(calls("/remove"), { team_id: ctx.teamId, target_team_id: member.team_id })) }, t("alliance.remove"))
            : null,
        ),
  );
}

/** A search of the team directory by the start of a name: each team found has a button that invites it. */
function teamSearchCard() {
  const results = h("ul", { class: "rows", id: "team-results", "aria-label": t("alliance.search_results") });
  const query = field("search-team", t("alliance.search_name"), { maxlength: "24", autocomplete: "off", required: true });
  const card = formCard(shell, {
    id: "team-search-form",
    title: "alliance.search",
    rows: [h("p", { class: "hint" }, t("alliance.search_hint")), query.row, results],
    submit: "alliance.search_submit",
    run: async (self) => {
      const found = (await api.get(`/api/team-directory?prefix=${encodeURIComponent(query.input.value.trim())}`)).teams;
      self.note.textContent = found.length ? "" : t("alliance.search_none");
      replace(
        results,
        found.map((team) =>
          h(
            "li",
            { class: "row", "data-found": team.name },
            h("div", { class: "row-main" }, h("strong", {}, team.name), h("span", { class: "muted small" }, ` ${team.mine ? t("alliance.search_mine") : team.handle ?? ""}`.trimEnd())),
            h("div", { class: "row-actions" }, h("button", { type: "button", "aria-label": t("alliance.invite_label", { name: team.name }), onclick: () => act(() => api.post(calls("/invite"), { team_id: ctx.teamId, target_team_id: team.id })) }, t("alliance.invite_submit"))),
          ),
        ),
      );
    },
  });
  return card.form;
}

function inviteCards() {
  const cards = [];
  if (ctx.can.has("invite")) {
    cards.push(teamSearchCard());
    const number = field("invite-team", t("alliance.invite_number"), { type: "number", min: "1", required: true });
    cards.push(
      formCard(shell, {
        id: "invite-form",
        title: "alliance.invite",
        rows: [h("p", { class: "hint" }, t("alliance.invite_hint")), number.row],
        submit: "alliance.invite_submit",
        run: async () => {
          await api.post(calls("/invite"), { team_id: ctx.teamId, target_team_id: Number(number.input.value) });
          await redraw();
        },
      }).form,
    );
  }
  if (view.invited?.length) {
    cards.push(
      h(
        "section",
        { class: "card", id: "invited" },
        h("h2", {}, t("alliance.invited_title")),
        h(
          "ul",
          { class: "rows", "aria-label": t("alliance.invited_title") },
          view.invited.map((each) =>
            h(
              "li",
              { class: "row", "data-invited": each.team },
              h("div", { class: "row-main" }, each.team),
              ctx.can.has("withdraw")
                ? h("div", { class: "row-actions" }, h("button", { type: "button", "aria-label": t("alliance.withdraw_label", { name: each.team }), onclick: () => act(() => api.post(calls("/withdraw"), { team_id: ctx.teamId, target_team_id: each.team_id })) }, t("alliance.withdraw")))
                : null,
            ),
          ),
        ),
      ),
    );
  }
  return cards;
}

function leaving() {
  const done = () => location.assign("/alliances/");
  return h(
    "section",
    { class: "card" },
    h("div", { class: "row-actions" },
      confirmButton(shell, problem, { id: "leave", label: t("alliance.leave"), sure: t("alliance.leave_sure"), run: async () => {
        await api.post(calls("/leave"), { team_id: ctx.teamId });
        done();
      } }),
      ctx.can.has("disband")
        ? confirmButton(shell, problem, { id: "disband", label: t("alliance.disband"), sure: t("alliance.disband_sure"), run: async () => {
          await api.post(calls("/disband"), { team_id: ctx.teamId });
          done();
        } })
        : null),
  );
}

function actingAs() {
  if (homes.length < 2) return null;
  const select = h(
    "select",
    { id: "act-as", onchange: () => location.assign(`/alliance/${id}?team=${select.value}`) },
    homes.map((home) => h("option", { value: String(home.team_id), selected: home.team_id === ctx.teamId }, home.team)),
  );
  return h("div", { class: "field" }, h("label", { for: "act-as" }, t("alliance.act_as")), select);
}

let drawings = 0;

async function redraw() {
  const mine = (drawings += 1);
  const [asked, roster] = await Promise.all([api.get(calls(`?team_id=${ctx.teamId}`)), api.get(calls("/roster"))]);
  if (mine !== drawings) return; // a newer drawing was started while this one asked: it shows the later state
  [view, people] = [asked, roster];
  ctx.can = new Set(view.can);
  const cards = [];
  if (ctx.can.has("speak")) cards.push(await pageCard(shell, ctx));
  cards.push(await ballotsCard(shell, ctx), await relationsCard(shell, ctx));
  if (mine !== drawings) return;
  document.title = `${view.name} · ${shell.game.game ?? "TerraForma"}`;
  replace(
    main,
    h(
      "div",
      { class: "stack" },
      h("h1", {}, view.name),
      back,
      h("p", { id: "you" }, t("alliance.you", { team: homes.find((home) => home.team_id === ctx.teamId).team, role: view.you.role })),
      actingAs(),
      h("section", { class: "card" }, h("h2", {}, t("alliance.members")), h("ul", { class: "rows", "aria-label": t("alliance.members") }, people.map(memberRow))),
      inviteCards(),
      ...cards,
      leaving(),
      problem,
    ),
  );
}

function missing() {
  document.title = `${t("alliance.missing_title")} · ${shell.game.game ?? "TerraForma"}`;
  replace(main, h("h1", {}, t("alliance.missing_title")), h("p", { role: "alert" }, t("alliance.missing")), back);
}

await shell.start();
if (!(await shell.restore())) {
  location.replace("/account/?next=alliances"); // one login; it sends the player back to the alliances
} else {
  await attempt(shell, problem, async () => {
    homes = (await api.get("/api/alliances")).alliances.filter((row) => row.id === id);
    if (!homes.length) return missing();
    const asked = Number(new URLSearchParams(location.search).get("team"));
    ctx = { id, teamId: (homes.find((home) => home.team_id === asked) ?? homes[0]).team_id, can: new Set() };
    await redraw();
  });
  if (!main.firstChild) replace(main, problem, back);
  if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert", id: "module-problems" }, shell.problems.join(" ")));
}

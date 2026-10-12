// The player's own page settings: make the public page, write what it says, choose whether the handle is listed in the directory,
// and show or hide each team. A regular page, for any screen and orientation; the account page links here.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";
import { Shell } from "./shell.js";

const shell = new Shell("profile");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);
const problem = h("p", { class: "problem", role: "alert", id: "profile-problem" });
let view = null;

const address = (token) => `${location.origin}/p/${token}`;

/** Runs a call that answers with the whole profile, then draws it and says $done (in the element called $noteId). */
function change(noteId, doneKey, work) {
  return attempt(shell, problem, async () => {
    view = await work();
    draw();
    if (doneKey) document.getElementById(noteId).textContent = t(doneKey);
  });
}

function pageCard() {
  if (!view.token) {
    return h(
      "section",
      { class: "card", id: "profile-page" },
      h("p", { id: "profile-state" }, t("profile.none")),
      h("button", { type: "button", class: "primary", onclick: () => change("profile-note", null, () => api.post("/api/profile/token")) }, t("profile.make")),
    );
  }
  return h(
    "section",
    { class: "card", id: "profile-page" },
    h("p", { id: "profile-state" }, t("profile.public")),
    h("p", { id: "profile-address" }, `${t("profile.address")} `, h("a", { href: `/p/${view.token}` }, address(view.token))),
    h("button", { type: "button", onclick: () => change("profile-note", "profile.renewed", () => api.post("/api/profile/token")) }, t("profile.renew")),
    h("p", { class: "note", role: "status", id: "profile-note" }),
  );
}

function bioCard() {
  const bio = h("textarea", { id: "profile-bio", rows: "5", maxlength: "500" }, view.bio);
  return h(
    "form",
    {
      class: "card",
      id: "bio-form",
      onsubmit: (event) => {
        event.preventDefault();
        change("bio-note", "profile.bio_saved", () => api.put("/api/profile/bio", { bio: bio.value }));
      },
    },
    h("h2", {}, t("profile.bio")),
    h("div", { class: "field" }, h("label", { for: "profile-bio" }, t("profile.bio_label")), bio),
    h("p", { class: "note", role: "status", id: "bio-note" }),
    h("button", { type: "submit", class: "primary" }, t("profile.bio_save")),
  );
}

function directoryCard() {
  const box = h("input", {
    type: "checkbox",
    id: "profile-directory",
    checked: view.directory,
    onchange: () => change("directory-note", box.checked ? "profile.directory_on" : "profile.directory_off", () => api.put("/api/profile/directory", { enabled: box.checked })),
  });
  return h(
    "section",
    { class: "card" },
    h("label", { class: "check" }, box, ` ${t("profile.directory")}`),
    h("p", { class: "hint" }, t("profile.directory_hint")),
    h("p", { class: "note", role: "status", id: "directory-note" }),
  );
}

function teamRow(team) {
  const box = h("input", {
    type: "checkbox",
    "aria-label": t("profile.team_show_label", { name: team.name }),
    checked: team.visible,
    onchange: () => change("profile-note-team", null, () => api.put("/api/profile/team-visible", { team_id: team.id, visible: box.checked })),
  });
  return h(
    "li",
    { class: "row", "data-team": team.name },
    h("div", { class: "row-main" }, h("strong", {}, team.name), " ", h("span", { class: "muted small" }, t(team.visible ? "profile.team_shown" : "profile.team_hidden"))),
    h("div", { class: "row-actions" },
      h("label", { class: "check" }, box, ` ${t("profile.team_show")}`),
      team.visible && team.token ? h("a", { href: `/p/team/${team.token}`, "aria-label": t("profile.team_page", { name: team.name }) }, t("profile.team_page", { name: team.name })) : null,
      team.token ? h("button", { type: "button", "aria-label": t("profile.team_renew_label", { name: team.name }), onclick: () => change("profile-note-team", null, () => api.post("/api/profile/team-token", { team_id: team.id })) }, t("profile.renew")) : null),
  );
}

function teamsCard() {
  return h(
    "section",
    { class: "card" },
    h("h2", {}, t("profile.teams")),
    view.teams.length ? h("ul", { class: "rows", "aria-label": t("profile.teams") }, view.teams.map(teamRow)) : h("p", { class: "muted" }, t("profile.no_teams")),
    h("p", { class: "note", role: "status", id: "profile-note-team" }),
  );
}

function draw() {
  replace(
    main,
    h("div", { class: "stack" }, h("h1", {}, t("profile.title")), h("p", {}, h("a", { href: "/account/", id: "back-to-account" }, t("profile.back"))), pageCard(), bioCard(), directoryCard(), teamsCard(), problem),
  );
}

await shell.start();
document.title = `${t("profile.title")} · ${shell.game.game ?? "TerraForma"}`;
if (!(await shell.restore())) {
  location.replace("/account/?next=profile"); // one login; it sends the player back here
} else {
  await attempt(shell, problem, async () => {
    view = await api.get("/api/profile");
    draw();
  });
  if (!view) replace(main, problem);
  if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

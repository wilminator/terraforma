// The public pages, which need no login: a player's page (/p/{token}), a team's (/p/team/{token}) and an alliance's
// (/p/alliance/{token}). They show only what the server sends: what the owner chose to share, never an account.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { problemText } from "./forms.js";
import { Shell } from "./shell.js";

const shell = new Shell("public");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

const match = /^\/p\/(team|alliance)\/([^/]+)$/.exec(location.pathname);
const kind = match ? match[1] : "player";
const token = match ? match[2] : location.pathname.split("/").filter(Boolean).pop();

const bioText = (bio) => (bio ? h("p", { class: "bio" }, bio) : null);
const linked = (name, page, base) => (page ? h("a", { href: `${base}/${page}` }, name) : name);

function playerPage(page) {
  return {
    title: page.handle ?? t("public.player"),
    body: [
      bioText(page.bio),
      h("h2", {}, t("public.teams")),
      page.teams.length
        ? h("ul", { class: "rows", "aria-label": t("public.teams") }, page.teams.map((team) => h("li", { class: "row", "data-team": team.name }, linked(team.name, team.page, "/p/team"))))
        : h("p", { class: "muted" }, t("public.no_teams")),
    ],
  };
}

function teamPage(page) {
  return {
    title: page.name,
    body: [
      page.handle ? h("p", { id: "played-by" }, t("public.played_by", { handle: page.handle })) : null,
      h("h2", {}, t("public.alliances")),
      page.alliances.length
        ? h("ul", { class: "rows", "aria-label": t("public.alliances") }, page.alliances.map((alliance) => h("li", { class: "row", "data-alliance": alliance.name }, linked(alliance.name, alliance.page, "/p/alliance"))))
        : h("p", { class: "muted" }, t("public.no_alliances")),
    ],
  };
}

function alliancePage(page) {
  return {
    title: page.name,
    body: [
      bioText(page.bio),
      h("h2", {}, t("public.teams")),
      h(
        "ul",
        { class: "rows", "aria-label": t("public.teams") },
        page.teams.map((team) =>
          h(
            "li",
            { class: "row", "data-team": team.name },
            h("div", { class: "row-main" }, h("strong", {}, linked(team.name, team.page, "/p/team")), " ", h("span", { class: "muted small" }, team.role)),
            team.handle ? h("span", { class: "muted small" }, t("public.played_by", { handle: team.handle })) : null,
          ),
        ),
      ),
    ],
  };
}

const BUILD = { player: playerPage, team: teamPage, alliance: alliancePage };
const PATH = { player: "", team: "team/", alliance: "alliance/" };

await shell.start();
try {
  const { title, body } = BUILD[kind](await api.get(`/api/p/${PATH[kind]}${encodeURIComponent(token)}`));
  document.title = `${title} · ${shell.game.game ?? "TerraForma"}`;
  replace(main, h("div", { class: "stack" }, h("h1", {}, title), ...body));
} catch (error) {
  document.title = `${t("public.missing_title")} · ${shell.game.game ?? "TerraForma"}`;
  if (error?.status === 404) replace(main, h("h1", {}, t("public.missing_title")), h("p", { role: "alert" }, t("public.missing")));
  else replace(main, h("p", { class: "problem", role: "alert" }, problemText(shell, error)));
}
if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));

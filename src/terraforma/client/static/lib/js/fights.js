// The fight pages: the finished fights a person can watch again, as a regular page for any screen. Anyone sees the public ones (the coliseum
// list and a random best fight); a signed-in player also sees the fights they can view (their own, the players they follow, their alliances')
// and each of their teams' history. A fight opens as `?fight={number}` (a viewable one) or `?watch={public name}` (a public one) and is played
// back with the fight scene the game uses, fed by what the server stored (fight-source.js, replaySource) instead of a socket.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { FightScene } from "./fight-scene.js";
import { replaySource } from "./fight-source.js";
import { attempt } from "./forms.js";
import { Shell } from "./shell.js";
import { fitStage } from "./stage.js";

const shell = new Shell("fights");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);
const problem = h("p", { class: "problem", role: "alert", id: "fights-problem" });
const PAGE = 20;

const when = (at) => new Date(at).toLocaleString();
const query = new URLSearchParams(location.search);

/** A list that loads $load(before) and shows $row of each; "More" asks for the ones before the oldest shown, until a short page says there are no more. */
function listing({ id, title, none, load, row }) {
  const list = h("ul", { class: "rows", id, "aria-label": title });
  const more = h("button", { type: "button", id: `${id}-more`, hidden: true }, t("fights.more"));
  const empty = h("p", { class: "muted", id: `${id}-none`, hidden: true }, none);
  let oldest = null;
  let version = 0; // a reset (another team) makes an answer still on its way stale: it is dropped
  async function page() {
    const mine = version;
    const rows = await load(oldest);
    if (mine !== version) return;
    for (const each of rows) list.append(row(each));
    if (rows.length) oldest = Math.min(...rows.map((each) => each.id));
    empty.hidden = list.children.length > 0;
    more.hidden = rows.length < PAGE;
  }
  more.onclick = () => attempt(shell, problem, page);
  const card = h("section", { class: "card", id: `${id}-card` }, h("h2", {}, title), list, empty, more);
  return { card, start: () => attempt(shell, problem, page), reset() { version += 1; replace(list); oldest = null; return page(); } };
}

const watchLink = (href, ...text) => h("a", { href, class: "watch" }, ...text);

const publicRow = (each) =>
  h("li", { class: "row", "data-fight": each.id }, h("div", { class: "row-main" }, watchLink(`?watch=${each.guid}`, t("fights.watch")), " ", h("span", { class: "muted small" }, when(each.at))));

const viewableRow = (each) =>
  h(
    "li",
    { class: "row", "data-fight": each.id },
    h("div", { class: "row-main" }, watchLink(`?fight=${each.id}`, t("fights.watch")), " ", h("span", { class: "muted small" }, [when(each.at), ...each.why.map((why) => t(`fights.why_${why}`)), each.over ? "" : t("fights.running")].filter(Boolean).join(" · "))),
  );

/** A team's fight; $keep(fight, kept) keeps it by hand (or lets it go) and redraws the row from the server's answer. */
const teamRow = (each, keep) => {
  const notes = [when(each.at), t(each.won ? "fights.won" : "fights.lost"), each.pvp ? t("fights.pvp") : t("fights.pve"), ...each.tags.map((tag) => t(`fights.tag_${tag}`)), each.kept ? t("fights.kept") : ""];
  const label = t(each.kept ? "fights.release" : "fights.keep");
  return h(
    "li",
    { class: "row", "data-fight": each.id, "data-kept": String(Boolean(each.kept)) },
    h("div", { class: "row-main" }, watchLink(`?fight=${each.id}`, t("fights.watch")), " ", h("span", { class: "muted small" }, notes.filter(Boolean).join(" · "))),
    h("button", { type: "button", class: "keep", "aria-label": t(each.kept ? "fights.release_fight" : "fights.keep_fight", { id: each.id }), onclick: () => keep(each, !each.kept) }, label),
  );
};

/** The page's lists. */
async function lists() {
  document.title = `${t("fights.title")} · ${shell.game.game ?? "TerraForma"}`;
  const best = h(
    "section",
    { class: "card", id: "fights-best" },
    h("h2", {}, t("fights.best")),
    h("p", { class: "hint" }, t("fights.best_hint")),
    h("div", { class: "row-actions" }, ["pve_underdog", "pve_overkill", "pvp_underdog", "pvp_overkill"].map((kind) =>
      h("button", { type: "button", "data-kind": kind, onclick: () => attempt(shell, problem, async () => location.assign(`?watch=${(await api.get(`/api/public/fights/showcase?kind=${kind}`)).guid}`)) }, t(`fights.tag_${kind}`)))),
  );
  const coliseum = listing({ id: "fights-public", title: t("fights.public"), none: t("fights.no_public"), load: (before) => api.get(`/api/public/fights/coliseum?limit=${PAGE}${before ? `&before=${before}` : ""}`), row: publicRow });
  const cards = [h("h1", {}, t("fights.title")), problem, best, coliseum.card];
  const started = [coliseum.start];
  const extras = [];
  if (shell.account) {
    const viewable = listing({ id: "fights-viewable", title: t("fights.viewable"), none: t("fights.no_viewable"), load: (before) => api.get(`/api/fight-pages/viewable?limit=${PAGE}${before ? `&before=${before}` : ""}`), row: viewableRow });
    const teams = await api.get("/api/teams");
    let team = teams[0]?.id ?? null;
    const tags = h("div", { id: "fights-tags" });
    /** The team's best fight of each kind and how many it keeps by hand, as the server has them. */
    const showTags = async () => {
      if (team === null) return replace(tags);
      const held = await api.get(`/api/teams/${team}/fight-tags`);
      const best = Object.entries(held.tags).filter(([, tag]) => tag !== null);
      replace(
        tags,
        h("p", { class: "muted small", id: "fights-kept-count" }, t("fights.kept_count", { count: held.kept.length, slots: held.slots })),
        best.length
          ? h("ul", { class: "rows", id: "fights-tag-list", "aria-label": t("fights.tags") }, best.map(([kind, tag]) => h("li", { class: "row", "data-kind": kind }, watchLink(`?fight=${tag.fight_id}`, t(`fights.tag_${kind}`)), " ", h("span", { class: "muted small" }, t("fights.score", { score: tag.score })))))
          : h("p", { class: "muted", id: "fights-tags-none" }, t("fights.no_tags")),
      );
    };
    const history = listing({ id: "fights-team", title: t("fights.team"), none: t("fights.no_team"), load: (before) => (team === null ? [] : api.get(`/api/teams/${team}/fights?limit=${PAGE}${before ? `&before=${before}` : ""}`)), row: (each) => teamRow(each, keep) });
    function keep(each, kept) {
      attempt(shell, problem, async () => {
        await api.post(`/api/teams/${team}/${kept ? "kept" : "kept/remove"}`, { fight_id: each.id });
        const row = history.card.querySelector(`[data-fight="${each.id}"]`);
        row.replaceWith(teamRow({ ...each, kept }, keep));
        await showTags();
      });
    }
    const pick = h("select", { id: "fights-team-pick", "aria-label": t("fights.team_pick"), onchange: () => { team = Number(pick.value); attempt(shell, problem, async () => { await showTags(); await history.reset(); }); } }, teams.map((each) => h("option", { value: each.id }, each.name)));
    history.card.querySelector("h2").after(teams.length ? pick : "", tags);
    extras.push(() => attempt(shell, problem, showTags));
    cards.push(viewable.card, history.card);
    started.push(viewable.start, history.start);
  }
  replace(main, cards);
  for (const start of [...started, ...extras]) start();
}

/** Plays the fight at $path back, on a stage of its own. */
async function replay(path) {
  const back = h("a", { href: location.pathname, id: "fights-back" }, t("fights.back"));
  const canvas = h("div", { class: "stage-canvas", id: "stage-canvas" });
  const stage = h("div", { class: "stage", id: "stage", role: "application", tabindex: "0", "aria-label": t("fights.stage") }, canvas);
  const note = h("p", { class: "stage-note", id: "stage-note", role: "status" });
  const frame = h("div", { class: "stage-frame replay" }, stage, note);
  document.title = `${t("fights.replay")} · ${shell.game.game ?? "TerraForma"}`;
  replace(main, h("h1", {}, t("fights.replay")), problem, h("p", {}, back), frame);
  let played;
  await attempt(shell, problem, async () => {
    played = await api.get(path);
  });
  if (!played) return;
  fitStage(frame, stage, canvas);
  const scene = new FightScene(shell, { stage, host: canvas, source: replaySource(played) });
  stage.dataset.scene = "fight";
  stage.addEventListener("fight-leave", () => location.assign(location.pathname));
  if (!(await scene.enter(played.fight.id, (text) => (note.textContent = text)))) problem.textContent = note.textContent;
}

await shell.start();
await shell.restore().catch(() => null);
const numbered = /^[1-9][0-9]{0,17}$/.exec(query.get("fight") ?? "");
const named = /^[0-9a-f]{32}$/.exec(query.get("watch") ?? "");
if (numbered) await replay(`/api/fight-pages/${numbered[0]}/replay`);
else if (named) await replay(`/api/public/fights/${named[0]}/replay`);
else await attempt(shell, problem, lists);
if (!main.firstChild) replace(main, problem);
if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));

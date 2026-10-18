// The guild's three screens, for an NPC dialog that reaches the tag `add_team`, `find_party` or `party_requests`: add one of your own
// teams to the hero's party, find an open party and ask to join it, and (for the party's leader) accept or decline the teams that asked.
// The server runs the guild: it says which teams and parties are on offer and refuses, in its own words, what the rules do not allow.
// Adding a team and asking a party go on with the dialog (the answer carries its next frame); answering requests stays where it is.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function guildActivity({ shell, heroId, prompt, finish, proceed, problem }) {
  const t = (key, values) => shell.text.get(key, values);
  const root = h("div", { class: "guild", id: "guild" });
  const note = h("p", { class: "note", role: "status", id: "guild-note" });
  const guild = `/api/heroes/${heroId}/dialog/guild`;
  const leave = h("button", { type: "button", class: "quiet", id: "guild-done", onclick: () => attempt(shell, problem, finish) }, t("guild.done"));

  async function load() {
    draw(await api.get(guild));
  }

  /** Adds a team or asks a party: the guild goes on with the dialog, so what comes back is the conversation's next frame. */
  function goOn(path, body) {
    note.textContent = "";
    attempt(shell, problem, async () => proceed((await api.post(`${guild}/${path}`, body)).dialog));
  }

  function answer(request, accept) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      await api.post(`${guild}/answer`, { request_id: request.id, accept });
      await load();
      note.textContent = t(accept ? "guild.accepted" : "guild.declined", { team: request.team });
    });
  }

  const teamRow = (team) =>
    h("li", { class: "row", "data-team": team.name }, h("strong", {}, team.name), h("button", { type: "button", "aria-label": t("guild.add_team", { name: team.name }), onclick: () => goOn("add-team", { team_id: team.id }) }, t("guild.add")));

  const partyRow = (party) => {
    const names = party.teams.map((team) => team.name ?? t("guild.hidden_team")).join(", ");
    return h(
      "li",
      { class: "row", "data-party": party.id },
      h("span", {}, h("strong", {}, names), ` ${t("guild.party_size", { count: party.heroes })}`),
      h("button", { type: "button", "aria-label": t("guild.ask_party", { id: party.id }), onclick: () => goOn("ask", { party_id: party.id }) }, t("guild.ask")),
    );
  };

  const requestRow = (request) =>
    h(
      "li",
      { class: "row", "data-request": request.id },
      h("span", {}, h("strong", {}, request.team), ` ${t("guild.party_size", { count: request.heroes })}`),
      h(
        "div",
        { class: "inline" },
        h("button", { type: "button", "aria-label": t("guild.accept_team", { name: request.team }), onclick: () => answer(request, true) }, t("guild.accept")),
        h("button", { type: "button", "aria-label": t("guild.decline_team", { name: request.team }), onclick: () => answer(request, false) }, t("guild.decline")),
      ),
    );

  function draw(view) {
    const [title, none, rows, row] = {
      add_team: ["guild.add_title", "guild.no_teams", view.teams, teamRow],
      find_party: ["guild.find_title", "guild.no_parties", view.parties, partyRow],
      party_requests: ["guild.requests_title", "guild.no_requests", view.requests, requestRow],
    }[view.command];
    replace(
      root,
      h("h3", {}, t(title)),
      rows.length ? h("ul", { class: "rows", "aria-label": t(title) }, rows.map(row)) : h("p", { class: "muted", id: "guild-none" }, t(none)),
      note,
      leave,
    );
  }

  attempt(shell, problem, load);
  return root;
}

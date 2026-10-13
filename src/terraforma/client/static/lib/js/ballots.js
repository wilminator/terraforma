// An alliance's ballots: the open ones first, a vote for each option, closing one early, and putting a new question.
// What each team voted shows on a public ballot; a secret ballot shows the turnout until it closes, then the totals.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt, field, formCard } from "./forms.js";

export async function ballotsCard(shell, ctx) {
  const t = (key, values) => shell.text.get(key, values);
  const base = `/api/alliances/${ctx.id}/ballots`;
  const box = h("section", { class: "card", id: "ballots" });
  const problem = h("p", { class: "problem", role: "alert" });
  let ballots = [];

  const reload = async () => {
    ballots = await api.get(`${base}?team_id=${ctx.teamId}`);
    draw();
  };

  const act = (work) => attempt(shell, problem, async () => {
    await work();
    await reload();
  });

  function optionRow(ballot, option, index) {
    const total = ballot.totals?.[index];
    const voted = ballot.your_option === index;
    const canVote = ctx.can.has("vote") && !ballot.closed && !(ballot.secret && ballot.you_voted);
    return h(
      "li",
      { class: "inline" },
      total === undefined ? (canVote ? null : option) : t("ballots.total", { option, count: total }),
      canVote
        ? h("button", { type: "button", "aria-pressed": voted ? "true" : "false", onclick: () => act(() => api.post(`${base}/${ballot.id}/vote`, { team_id: ctx.teamId, option: index })) }, t("ballots.vote", { option }))
        : null,
    );
  }

  function ballotRow(ballot) {
    const yours = ballot.secret ? (ballot.you_voted ? t("ballots.you_voted_secret") : null) : ballot.your_option !== null && ballot.your_option !== undefined ? t("ballots.you_voted", { option: ballot.options[ballot.your_option] }) : null;
    const winner = ballot.closed ? (ballot.result.winner === null || ballot.result.winner === undefined ? t("ballots.no_winner") : t("ballots.winner", { option: ballot.options[ballot.result.winner] })) : null;
    return h(
      "li",
      { class: "row", "data-ballot": ballot.title },
      h("div", { class: "row-main" }, h("strong", {}, ballot.title), " ", h("span", { class: "muted small" }, t(ballot.closed ? "ballots.closed" : "ballots.open")), ballot.secret ? h("span", { class: "muted small" }, ` · ${t("ballots.secret")}`) : null),
      h("ul", { class: "rows", "aria-label": ballot.title }, ballot.options.map((option, index) => optionRow(ballot, option, index))),
      h("p", { class: "muted small" }, t(ballot.turnout === 1 ? "ballots.cast" : "ballots.cast_many", { count: ballot.turnout })),
      yours ? h("p", {}, yours) : null,
      winner ? h("p", {}, winner) : null,
      ctx.can.has("close_ballot") && !ballot.closed
        ? h("div", { class: "row-actions" }, h("button", { type: "button", onclick: () => act(() => api.post(`${base}/${ballot.id}/close`, { team_id: ctx.teamId })) }, t("ballots.close")))
        : null,
    );
  }

  function newBallot() {
    const question = field("ballot-question", t("ballots.question"), { maxlength: "120", required: true, autocomplete: "off" });
    const options = h("textarea", { id: "ballot-options", rows: "4" });
    const secret = h("input", { type: "checkbox", id: "ballot-secret" });
    const hours = field("ballot-hours", t("ballots.hours"), { type: "number", min: "1", max: "720" });
    return formCard(shell, {
      id: "ballot-form",
      title: "ballots.new",
      rows: [
        question.row,
        h("div", { class: "field" }, h("label", { for: "ballot-options" }, t("ballots.options")), options),
        h("label", { class: "check" }, secret, ` ${t("ballots.is_secret")}`),
        hours.row,
      ],
      submit: "ballots.open_submit",
      run: async () => {
        const body = { team_id: ctx.teamId, title: question.input.value, options: options.value.split("\n").map((line) => line.trim()).filter(Boolean), secret: secret.checked };
        if (hours.input.value) body.closes_in_hours = Number(hours.input.value);
        await api.post(base, body);
        await reload();
      },
    }).form;
  }

  function draw() {
    replace(
      box,
      h("h2", {}, t("ballots.title")),
      ballots.length ? h("ul", { class: "rows", "aria-label": t("ballots.title") }, ballots.map(ballotRow)) : h("p", { class: "muted" }, t("ballots.none")),
      problem,
      ctx.can.has("open_ballot") ? newBallot() : null,
    );
  }

  await reload();
  return box;
}

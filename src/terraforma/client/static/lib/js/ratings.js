// The Ratings panel: the questions asked after a fight ("another team helped or harmed yours; what do you think of them?"). The player rates
// the other team on the game's scale, accepts the change the game's rule suggested (when it made one) or dismisses the question. The score that
// results is the game's rule (Relations.resolve), so the browser shows the server's answer and never works one out.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function ratingsPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert", id: "ratings-problem" });
  const note = h("p", { class: "note", role: "status", id: "ratings-note" });
  const body = h("div", { id: "ratings-body" });
  const root = h("div", { class: "panel ratings", id: "panel-ratings" }, h("h2", {}, t("panel.ratings")), body);
  let asked = { bands: [], questions: [] }; // GET /api/ratings
  const typed = new Map(); // question id -> the score typed in, kept across redraws

  async function load() {
    asked = await api.get("/api/ratings");
    draw();
  }

  function refresh() {
    attempt(shell, problem, load);
  }

  /** An answer; the questions are read again after, so what is shown is the server's. */
  function send(question, path, values, done) {
    note.textContent = "";
    attempt(shell, problem, async () => {
      const result = await api.post(`/api/ratings/${question.id}/${path}`, values);
      typed.delete(question.id);
      await load();
      note.textContent = done(result);
    });
  }

  const sentence = (question) =>
    question.interaction === "asked"
      ? t("ratings.asked", { team: question.team.name, other: question.other.name, reason: question.reason })
      : t(`ratings.${question.interaction}`, { team: question.team.name, other: question.other.name });

  function row(question) {
    const score = h("input", {
      type: "number",
      min: "-100",
      max: "100",
      value: typed.get(question.id) ?? "",
      "aria-label": t("ratings.score", { name: question.other.name }),
      oninput: (event) => typed.set(question.id, event.target.value),
    });
    const rate = () => {
      if (score.value.trim() === "") {
        problem.textContent = t("ratings.choose");
        return;
      }
      send(question, "answer", { score: Number(score.value) }, (result) => t("ratings.rated", { name: result.name, band: result.band, score: result.score }));
    };
    return h(
      "li",
      { class: "row", "data-question": question.id },
      h("p", {}, sentence(question)),
      h(
        "div",
        { class: "inline" },
        score,
        h("button", { type: "button", "aria-label": t("ratings.rate_name", { name: question.other.name }), onclick: rate }, t("ratings.rate")),
        question.suggested !== null
          ? h("button", { type: "button", "aria-label": t("ratings.accept_name", { name: question.other.name }), onclick: () => send(question, "answer", { accept: true }, (result) => t("ratings.rated", { name: result.name, band: result.band, score: result.score })) }, t("ratings.accept", { change: question.suggested > 0 ? `+${question.suggested}` : String(question.suggested) }))
          : null,
        h("button", { type: "button", "aria-label": t("ratings.dismiss_name", { name: question.other.name }), onclick: () => send(question, "dismiss", {}, () => t("ratings.dismissed")) }, t("ratings.dismiss")),
      ),
    );
  }

  function draw() {
    const scale = asked.bands.map((band) => `${band.name} (${band.low} to ${band.high})`).join(", ");
    replace(
      body,
      h("div", { class: "inline" }, h("button", { type: "button", onclick: refresh }, t("panel.refresh"))),
      h("p", { class: "muted small", id: "ratings-scale" }, t("ratings.scale", { scale })),
      asked.questions.length ? h("ul", { class: "rows", id: "ratings-list", "aria-label": t("panel.ratings") }, asked.questions.map(row)) : h("p", { class: "muted", id: "ratings-none" }, t("ratings.none")),
      problem,
      note,
    );
  }

  draw();
  return { element: root, show: refresh };
}

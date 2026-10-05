// Small pieces the account pages share: a labelled field, a form card that shows what the server said, and the words for a refusal.

import { ApiError } from "./api.js";
import { h } from "./dom.js";

/** A labelled input: { row, input }. */
export function field(id, label, attributes = {}) {
  const input = h("input", { id, name: id, ...attributes });
  return { row: h("div", { class: "field" }, h("label", { for: id }, label), input), input };
}

/** What a failed call says to the player: the server's own words, or how long to wait when a rate limit stopped it. */
export function problemText(shell, error) {
  if (!(error instanceof ApiError)) throw error;
  return error.status === 429 && error.retryAfter ? shell.text.get("problem.retry", { seconds: error.retryAfter }) : error.message;
}

/**
 * A card with a form: $rows go in, a button submits, and $run (async) does the call. A refusal is shown under the fields;
 * $card.note is where $run puts what went well. Returns { form, problem, note, button }.
 */
export function formCard(shell, { id, title, rows = [], submit, run }) {
  const t = (key, values) => shell.text.get(key, values);
  const problem = h("p", { class: "problem", role: "alert" });
  const note = h("p", { class: "note", role: "status" });
  const button = h("button", { type: "submit", class: "primary" }, t(submit));
  const card = { problem, note, button };
  card.form = h(
    "form",
    {
      class: "card",
      id,
      onsubmit: async (event) => {
        event.preventDefault();
        problem.textContent = "";
        note.textContent = "";
        button.disabled = true;
        button.textContent = t("form.busy");
        try {
          await run(card);
        } catch (error) {
          problem.textContent = problemText(shell, error);
        } finally {
          button.disabled = false;
          button.textContent = t(submit);
        }
      },
    },
    title ? h("h2", {}, t(title)) : null,
    ...rows,
    problem,
    note,
    button,
  );
  return card;
}

/** A card that only says something (and may link on). */
export function messageCard(shell, titleKey, textKey, values, ...more) {
  return h("section", { class: "card", role: "status" }, h("h2", {}, shell.text.get(titleKey)), h("p", {}, shell.text.get(textKey, values)), ...more);
}

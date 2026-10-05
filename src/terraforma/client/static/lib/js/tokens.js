// The pages the emailed links open: confirm the address, confirm a new address, confirm a two-factor change, choose a new password.
// Nothing happens on opening a link (mail scanners open them): the player presses the button.

import { post } from "./api.js";
import { h } from "./dom.js";
import { formCard } from "./forms.js";

/** A card with a button; pressing it runs $run, and $done (given the answer) draws what came of it. */
export function confirmCard(shell, { id, title, explain, submit, run, done }) {
  const t = (key, values) => shell.text.get(key, values);
  const card = formCard(shell, {
    id,
    title,
    submit,
    rows: [h("p", {}, t(explain))],
    run: async () => done(await run()),
  });
  return card.form;
}

export const confirmEmail = (shell, token, done) =>
  confirmCard(shell, {
    id: "confirm-email",
    title: "confirm.email_title",
    explain: "confirm.email_explain",
    submit: "confirm.email_submit",
    run: () => post("/api/confirm-email", { token }),
    done,
  });

export const confirmEmailChange = (shell, token, done) =>
  confirmCard(shell, {
    id: "change-email",
    title: "confirm.change_title",
    explain: "confirm.change_explain",
    submit: "confirm.change_submit",
    run: () => post("/api/email-change/complete", { token }),
    done,
  });

export const confirmTwoFactor = (shell, token, done) =>
  confirmCard(shell, {
    id: "confirm-2fa",
    title: "confirm.twofa_title",
    explain: "confirm.twofa_explain",
    submit: "confirm.twofa_submit",
    run: () => post("/api/2fa/confirm", { token }),
    done,
  });

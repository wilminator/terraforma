// Registration and the password reset: both ask for an email address, answer the same whatever the address, and finish by link.

import { post } from "./api.js";
import { field, formCard, messageCard } from "./forms.js";
import { h } from "./dom.js";

const NEW_PASSWORD = { type: "password", autocomplete: "new-password", required: true, minlength: 12, maxlength: 1024 };

/** A new account; $onDone gets the card that says the email is on its way. */
export function registerForm(shell, onDone) {
  const t = (key, values) => shell.text.get(key, values);
  const username = field("register-username", t("login.username"), { autocomplete: "username", required: true, minlength: 3, maxlength: 32 });
  const email = field("register-email", t("register.email"), { type: "email", autocomplete: "email", required: true, maxlength: 254 });
  const password = field("register-password", t("login.password"), NEW_PASSWORD);
  const again = field("register-again", t("register.again"), NEW_PASSWORD);
  const card = formCard(shell, {
    id: "register",
    title: "register.title",
    submit: "register.submit",
    rows: [username.row, email.row, password.row, h("p", { class: "hint" }, t("register.password_hint")), again.row],
    run: async ({ problem }) => {
      if (password.input.value !== again.input.value) {
        problem.textContent = t("register.mismatch");
        return;
      }
      await post("/api/register", { username: username.input.value.trim(), email: email.input.value.trim(), password: password.input.value });
      onDone(messageCard(shell, "register.sent_title", "register.sent", { email: email.input.value.trim() }));
    },
  });
  queueMicrotask(() => username.input.focus());
  return card.form;
}

/** Asks for a reset link. The server answers the same whether or not the address has an account, and so does this. */
export function resetRequestForm(shell, onDone) {
  const t = (key, values) => shell.text.get(key, values);
  const email = field("reset-email", t("register.email"), { type: "email", autocomplete: "email", required: true, maxlength: 254 });
  const card = formCard(shell, {
    id: "reset-request",
    title: "reset.title",
    submit: "reset.submit",
    rows: [h("p", {}, t("reset.explain")), email.row],
    run: async () => {
      await post("/api/password-reset", { email: email.input.value.trim() });
      onDone(messageCard(shell, "reset.sent_title", "reset.sent", { email: email.input.value.trim() }));
    },
  });
  queueMicrotask(() => email.input.focus());
  return card.form;
}

/** Choosing the new password, from the link in the mail. */
export function resetCompleteForm(shell, token, onDone) {
  const t = (key, values) => shell.text.get(key, values);
  const password = field("reset-password", t("reset.new_password"), NEW_PASSWORD);
  const again = field("reset-again", t("register.again"), NEW_PASSWORD);
  const card = formCard(shell, {
    id: "reset-complete",
    title: "reset.complete_title",
    submit: "reset.complete_submit",
    rows: [password.row, h("p", { class: "hint" }, t("register.password_hint")), again.row],
    run: async ({ problem }) => {
      if (password.input.value !== again.input.value) {
        problem.textContent = t("register.mismatch");
        return;
      }
      await post("/api/password-reset/complete", { token, password: password.input.value });
      onDone();
    },
  });
  queueMicrotask(() => password.input.focus());
  return card.form;
}

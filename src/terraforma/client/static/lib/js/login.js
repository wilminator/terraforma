// The login form, shared by the game and the account page. Asks for a second code when the account has 2FA on.

import { ApiError, post } from "./api.js";
import { h } from "./dom.js";

/** Returns the form; $onSignedIn is called with the answer of a good login. */
export function loginForm(shell, onSignedIn) {
  const t = (key, values) => shell.text.get(key, values);
  const username = h("input", { id: "login-username", name: "username", autocomplete: "username", required: true, maxlength: 32 });
  const password = h("input", { id: "login-password", name: "password", type: "password", autocomplete: "current-password", required: true });
  const code = h("input", { id: "login-code", name: "code", autocomplete: "one-time-code", maxlength: 32, inputmode: "text" });
  const codeRow = h("div", { class: "field", hidden: true }, h("label", { for: "login-code" }, t("login.code")), code);
  const problem = h("p", { class: "problem", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary" }, t("login.submit"));

  const form = h(
    "form",
    {
      class: "card login",
      onsubmit: async (event) => {
        event.preventDefault();
        problem.textContent = "";
        submit.disabled = true;
        submit.textContent = t("login.busy");
        try {
          const body = { username: username.value.trim(), password: password.value };
          if (!codeRow.hidden) body.code = code.value.trim();
          const answer = await post("/api/login", body);
          if (answer.needs_code) {
            codeRow.hidden = false;
            code.required = true;
            code.focus();
            return;
          }
          password.value = "";
          onSignedIn(answer);
        } catch (error) {
          if (!(error instanceof ApiError)) throw error;
          problem.textContent = error.status === 429 && error.retryAfter ? t("login.retry", { seconds: error.retryAfter }) : error.message;
        } finally {
          submit.disabled = false;
          submit.textContent = t("login.submit");
        }
      },
    },
    h("h2", {}, t("login.title")),
    h("div", { class: "field" }, h("label", { for: "login-username" }, t("login.username")), username),
    h("div", { class: "field" }, h("label", { for: "login-password" }, t("login.password")), password),
    codeRow,
    problem,
    submit,
  );
  queueMicrotask(() => username.focus());
  return form;
}

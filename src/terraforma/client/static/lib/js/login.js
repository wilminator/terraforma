// The login form, shared by the game and the account page. After the password it asks for the second factor when the account
// has one: a code, or a security key (and, if the game allows it, "remember this browser").

import { ApiError, post } from "./api.js";
import { h, replace } from "./dom.js";
import { secondFactorRows } from "./second-factor.js";

/** Returns the form; $onSignedIn is called with the answer of a good login (it carries the game's note, if there is one). */
export function loginForm(shell, onSignedIn) {
  const t = (key, values) => shell.text.get(key, values);
  const username = h("input", { id: "login-username", name: "username", autocomplete: "username", required: true, maxlength: 32 });
  const password = h("input", { id: "login-password", name: "password", type: "password", autocomplete: "current-password", required: true });
  const second = h("div", { class: "second", hidden: true });
  const problem = h("p", { class: "problem", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary" }, t("login.submit"));
  let asking = null; // the second-factor rows, once the password was right

  /** Sends the login with $extra (a code or a key's answer); draws the second factor when the server asks for one. */
  const attempt = async (extra = {}) => {
    problem.textContent = "";
    submit.disabled = true;
    submit.textContent = t("login.busy");
    try {
      const body = { username: username.value.trim(), password: password.value, ...extra };
      if (asking && asking.remember()) body.remember = true;
      if (asking && !extra.assertion && asking.code.value.trim()) body.code = asking.code.value.trim();
      const answer = await post("/api/login", body);
      if (answer.needs_code) {
        asking = secondFactorRows(shell, { offer: answer, prefix: "login", submit: attempt, fresh: () => post("/api/login", { username: username.value.trim(), password: password.value }) });
        replace(second, ...asking.rows);
        second.hidden = false;
        asking.focus();
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
  };

  const form = h(
    "form",
    {
      class: "card login",
      onsubmit: (event) => {
        event.preventDefault();
        return attempt();
      },
    },
    h("h2", {}, t("login.title")),
    h("div", { class: "field" }, h("label", { for: "login-username" }, t("login.username")), username),
    h("div", { class: "field" }, h("label", { for: "login-password" }, t("login.password")), password),
    second,
    problem,
    submit,
  );
  queueMicrotask(() => username.focus());
  return form;
}

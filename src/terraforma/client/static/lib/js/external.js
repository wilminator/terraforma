// Signing in with an external provider (Google, Microsoft, ...): the buttons, the step after a first sign-in, the two-factor step,
// and the account page's card for linking, unlinking and a first password. The provider's own pages are the server's business:
// a button is a plain link to /api/external/<name>/start, and the server sends the browser back to /account/?external=<outcome>.

import { get, post } from "./api.js";
import { h, replace } from "./dom.js";
import { field, formCard, problemText } from "./forms.js";
import { proofField, secondFactorRows } from "./second-factor.js";

/** The "Continue with ..." links under the login form; nothing when the game sets up no provider. $next is a page name (play, profile, oidc) or null; with oidc, $request is the sign-in provider's signed request. */
export async function providerButtons(shell, next = null, request = null) {
  const { providers } = await get("/api/external/providers");
  if (!providers.length) return null;
  const query = next ? `?next=${encodeURIComponent(next)}${request ? `&request=${encodeURIComponent(request)}` : ""}` : "";
  return h(
    "div",
    { class: "card providers", id: "providers" },
    h("h2", {}, shell.text.get("external.title")),
    providers.map((provider) => h("p", {}, h("a", { class: "button", href: `/api/external/${provider.name}/start${query}`, id: `sign-in-${provider.name}` }, shell.text.get("external.continue", { provider: provider.label })))),
  );
}

/** The words for a failed trip (the address says a reason in one word). */
export function errorText(shell, reason) {
  const known = ["denied", "expired", "email_in_use", "email_unverified", "taken", "failed"];
  return shell.text.get(`external.error_${known.includes(reason) ? reason : "failed"}`);
}

/** After a first sign-in: choose a username and handle (and, if wanted, a password). $onDone gets the login answer. */
export function finishCard(shell, pending, onDone) {
  const t = (key, values) => shell.text.get(key, values);
  const username = field("finish-username", t("login.username"), { autocomplete: "username", required: true, maxlength: 32 });
  const handle = field("finish-handle", t("handle.label"), { autocomplete: "nickname", required: true, maxlength: 24 });
  const password = field("finish-password", t("external.password_optional"), { type: "password", autocomplete: "new-password" });
  return formCard(shell, {
    id: "finish-form",
    title: "external.finish_title",
    submit: "external.finish_submit",
    rows: [
      h("p", {}, t("external.finish_explain", { provider: pending.provider, email: pending.email })),
      username.row,
      handle.row,
      h("p", { class: "hint" }, t("handle.hint")),
      password.row,
      h("p", { class: "hint" }, t("register.password_hint")),
    ],
    run: async () => {
      const body = { username: username.input.value.trim(), handle: handle.input.value.trim() };
      if (password.input.value) body.password = password.input.value;
      const answer = await post("/api/external/finish", body);
      password.input.value = "";
      onDone(answer);
    },
  }).form;
}

/** The provider vouched for the player, but the account has two-factor login on: a code or a security key. */
export function twoFactorStep(shell, onDone) {
  const t = (key, values) => shell.text.get(key, values);
  const rows = h("div", { class: "second" });
  let asking = null;
  const send = async (extra = {}) => {
    if (!asking) return;
    const body = { ...extra };
    if (asking.remember()) body.remember = true;
    if (!extra.assertion) body.code = asking.code.value.trim();
    const answer = await post("/api/external/2fa", body);
    asking.code.value = "";
    onDone(answer);
  };
  const card = formCard(shell, { id: "external-2fa-form", title: "external.twofa_title", submit: "login.submit", rows: [rows], run: () => send() });
  get("/api/external/2fa").then((offer) => {
    asking = secondFactorRows(shell, {
      offer,
      prefix: "external",
      submit: (extra) => send(extra).catch((error) => { card.problem.textContent = error.message; }),
      fresh: () => get("/api/external/2fa"),
    });
    replace(rows, ...asking.rows);
    asking.focus();
  }).catch((error) => { card.problem.textContent = problemText(shell, error); });
  return card.form;
}

/** On the account page: the providers linked to this account (each can be unlinked), the ones that can be linked, and a first password. */
export async function signInMethodsCard(shell, status, redraw) {
  const t = (key, values) => shell.text.get(key, values);
  const [{ providers }, { identities, has_password: hasPassword }] = await Promise.all([get("/api/external/providers"), get("/api/external/identities")]);
  if (!providers.length && !identities.length) return null;

  const problem = h("p", { class: "problem", role: "alert" });
  const proof = proofField(shell, status, "methods-code");
  const withCode = async (body) => ({ ...body, ...(await proof.collect()) });
  const attempt = async (work) => {
    problem.textContent = "";
    try {
      await work();
    } catch (error) {
      problem.textContent = problemText(shell, error);
    }
  };

  const linkedNames = new Set(identities.map((each) => each.provider));
  const rows = identities.map((each) =>
    h(
      "li",
      { class: "method" },
      h("span", {}, each.email ? `${each.label} (${each.email})` : each.label),
      " ",
      h("button", {
        type: "button",
        class: "unlink",
        id: `unlink-${each.provider}`,
        onclick: () =>
          attempt(async () => {
            await post("/api/external/unlink", await withCode({ provider: each.provider }));
            await redraw();
          }),
      }, t("external.unlink")),
    ),
  );
  const links = providers
    .filter((each) => !linkedNames.has(each.name))
    .map((each) =>
      h("button", {
        type: "button",
        id: `link-${each.name}`,
        onclick: () =>
          attempt(async () => {
            const answer = await post(`/api/external/${each.name}/link`, await withCode({}));
            location.assign(answer.url);
          }),
      }, t("external.link", { provider: each.label })),
    );

  const newPassword = field("methods-password", t("reset.new_password"), { type: "password", autocomplete: "new-password", required: true });
  const passwordForm = hasPassword
    ? null
    : h(
        "form",
        {
          class: "inner",
          id: "first-password-form",
          onsubmit: (event) => {
            event.preventDefault();
            attempt(async () => {
              await post("/api/external/password", await withCode({ password: newPassword.input.value }));
              newPassword.input.value = "";
              await redraw();
            });
          },
        },
        h("p", { class: "hint" }, t("external.password_explain")),
        newPassword.row,
        h("button", { type: "submit", class: "primary" }, t("external.password_submit")),
      );

  return h(
    "section",
    { class: "card", id: "sign-in-methods" },
    h("h2", {}, t("external.methods_title")),
    rows.length ? h("ul", {}, rows) : null,
    !hasPassword && rows.length ? h("p", { class: "hint" }, t("external.only_way")) : null,
    status.enabled ? h("p", { class: "hint" }, t("external.code_needed")) : null,
    proof.row,
    links.length ? h("p", {}, links) : null,
    passwordForm,
    problem,
  );
}

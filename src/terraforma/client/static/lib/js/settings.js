// The signed-in account's cards: the public handle, the email address and two-factor login.

import { get, post } from "./api.js";
import { h, replace } from "./dom.js";
import { field, formCard, problemText } from "./forms.js";

const CODE = { autocomplete: "one-time-code", required: true, maxlength: 32, inputmode: "text" };

/** The name other players see. */
export function handleCard(shell) {
  const t = (key, values) => shell.text.get(key, values);
  const current = h("p", { id: "current-handle" });
  const show = () => {
    current.textContent = shell.account.handle ? t("handle.current", { handle: shell.account.handle }) : t("handle.none");
  };
  show();
  const handle = field("handle", t("handle.label"), { autocomplete: "nickname", required: true, maxlength: 24 });
  const card = formCard(shell, {
    id: "handle-form",
    title: "handle.title",
    submit: "handle.submit",
    rows: [current, h("p", { class: "hint" }, t("handle.hint")), handle.row],
    run: async ({ note }) => {
      const answer = await post("/api/handle", { handle: handle.input.value.trim() });
      shell.account.handle = answer.handle;
      show();
      handle.input.value = "";
      note.textContent = t("handle.saved");
    },
  });
  return card.form;
}

/** Asks for a link to a new address; with two-factor login on, a live code as well (never the password). */
export function emailCard(shell, twoFactorOn) {
  const t = (key, values) => shell.text.get(key, values);
  const email = field("new-email", t("email.label"), { type: "email", autocomplete: "email", required: true, maxlength: 254 });
  const code = field("email-code", t("twofa.code"), CODE);
  code.row.hidden = !twoFactorOn;
  code.input.required = twoFactorOn;
  const card = formCard(shell, {
    id: "email-form",
    title: "email.title",
    submit: "email.submit",
    rows: [h("p", { class: "hint" }, t(twoFactorOn ? "email.hint_code" : "email.hint")), email.row, code.row],
    run: async ({ note }) => {
      const body = { email: email.input.value.trim() };
      if (twoFactorOn) body.code = code.input.value.trim();
      await post("/api/email-change", body);
      email.input.value = code.input.value = "";
      note.textContent = t("email.sent");
    },
  });
  return card.form;
}

/** Groups a secret in fours so it can be typed from the screen. */
const spaced = (secret) => secret.match(/.{1,4}/g).join(" ");

/** Two-factor login: set up the authenticator app, turn it on or off, and ask for new recovery codes. Every change is confirmed by a mailed link. */
export function twoFactorCard(shell, status) {
  const t = (key, values) => shell.text.get(key, values);
  const section = h("section", { class: "card", id: "twofa" });
  const draw = (...children) => replace(section, h("h2", {}, t("twofa.title")), ...children);

  /** A code-only form whose success is "a link is on its way". */
  const codeForm = (id, submit, path, explain) => {
    const code = field(`${id}-code`, t("twofa.code"), CODE);
    const card = formCard(shell, {
      id,
      submit,
      rows: [h("p", { class: "hint" }, t(explain)), code.row],
      run: async ({ note }) => {
        await post(path, { code: code.input.value.trim() });
        code.input.value = "";
        note.textContent = t("twofa.link_sent");
      },
    });
    card.form.className = "inner";
    return card.form;
  };

  const showOn = () =>
    draw(
      h("p", { id: "twofa-state" }, t("twofa.on", { left: status.recovery_codes_left })),
      codeForm("twofa-recovery", "twofa.recovery_submit", "/api/2fa/recovery-codes", "twofa.recovery_hint"),
      codeForm("twofa-disable", "twofa.disable_submit", "/api/2fa/disable", "twofa.disable_hint"),
    );

  const showSetup = (answer) => {
    const code = field("twofa-enable-code", t("twofa.code"), CODE);
    const card = formCard(shell, {
      id: "twofa-enable",
      submit: "twofa.enable_submit",
      rows: [
        h("p", {}, t("twofa.setup_explain")),
        h("p", {}, h("code", { id: "twofa-secret", class: "secret" }, spaced(answer.secret))),
        h("p", {}, h("a", { href: answer.uri, id: "twofa-uri" }, t("twofa.open_app"))),
        code.row,
      ],
      run: async ({ note }) => {
        await post("/api/2fa/enable", { code: code.input.value.trim() });
        code.input.value = "";
        note.textContent = t("twofa.link_sent");
      },
    });
    card.form.className = "inner";
    draw(card.form);
  };

  const showOff = () => {
    const problem = h("p", { class: "problem", role: "alert" });
    draw(
      h("p", { id: "twofa-state" }, t("twofa.off")),
      h("button", {
        type: "button",
        id: "twofa-setup",
        class: "primary",
        onclick: async () => {
          problem.textContent = "";
          try {
            showSetup(await post("/api/2fa/setup"));
          } catch (error) {
            problem.textContent = problemText(shell, error);
          }
        },
      }, t("twofa.setup")),
      problem,
    );
  };

  (status.enabled ? showOn : showOff)();
  return section;
}

/** The whole signed-in view. */
export async function accountView(shell, onLogout) {
  const t = (key, values) => shell.text.get(key, values);
  const status = await get("/api/2fa");
  return h(
    "div",
    { class: "stack" },
    h("p", { id: "who" }, t("account.signed_in", { username: shell.account.username })),
    h("div", { class: "cards" }, handleCard(shell), emailCard(shell, status.enabled), twoFactorCard(shell, status)),
    h("p", {}, h("button", { type: "button", id: "logout", onclick: onLogout }, t("account.logout"))),
  );
}

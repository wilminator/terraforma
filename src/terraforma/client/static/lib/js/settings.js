// The signed-in account's cards: the public handle, the email address and two-factor login.

import { get, post } from "./api.js";
import { h, replace } from "./dom.js";
import { signInMethodsCard } from "./external.js";
import { field, formCard, problemText } from "./forms.js";
import { proofField } from "./second-factor.js";
import { createCredential, supported } from "./webauthn.js";

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

/** Asks for a link to a new address; with two-factor login on, a live code or the security key as well (never the password). */
export function emailCard(shell, status) {
  const t = (key, values) => shell.text.get(key, values);
  const email = field("new-email", t("email.label"), { type: "email", autocomplete: "email", required: true, maxlength: 254 });
  const proof = proofField(shell, status, "email-code");
  const card = formCard(shell, {
    id: "email-form",
    title: "email.title",
    submit: "email.submit",
    rows: [h("p", { class: "hint" }, t(status.enabled ? "email.hint_code" : "email.hint")), email.row, proof.row],
    run: async ({ note }) => {
      await post("/api/email-change", { email: email.input.value.trim(), ...(await proof.collect()) });
      email.input.value = proof.input.value = "";
      note.textContent = t("email.sent");
    },
  });
  return card.form;
}

/** Groups a secret in fours so it can be typed from the screen. */
const spaced = (secret) => secret.match(/.{1,4}/g).join(" ");

const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : null);

/**
 * Two-factor login: the authenticator app, security keys and passkeys, recovery codes, remembered browsers, and turning it all off.
 * Every change is confirmed by a mailed link, and (once it is on) needs a code or the key first.
 */
export async function twoFactorCard(shell, status, redraw) {
  const t = (key, values) => shell.text.get(key, values);
  const section = h("section", { class: "card", id: "twofa" });
  const proof = proofField(shell, status, "twofa-proof-code");
  const problem = h("p", { class: "problem", role: "alert" });
  const note = h("p", { class: "note", role: "status" });
  const browsers = status.enabled ? (await get("/api/2fa/browsers")).browsers : [];

  /** Runs $work; a refusal is shown under the card, and what went well is $done's text. */
  const attempt = async (work, done = "twofa.link_sent") => {
    problem.textContent = note.textContent = "";
    try {
      await work();
      if (done) note.textContent = t(done);
    } catch (error) {
      problem.textContent = problemText(shell, error);
    }
  };
  const button = (id, label, onclick, className = null) => h("button", { type: "button", id, class: className, onclick }, t(label));
  /** A change that is confirmed by a mailed link: POSTs $path with the proof. */
  const linked = (path, extra = {}) => attempt(async () => post(path, { ...extra, ...(await proof.collect()) }));

  const showSetup = (answer) => {
    const code = field("twofa-enable-code", t("twofa.app_code"), { autocomplete: "one-time-code", required: true, maxlength: 32, inputmode: "text" });
    const card = formCard(shell, {
      id: "twofa-enable",
      submit: "twofa.enable_submit",
      rows: [
        h("p", {}, t("twofa.setup_explain")),
        h("p", {}, h("code", { id: "twofa-secret", class: "secret" }, spaced(answer.secret))),
        h("p", {}, h("a", { href: answer.uri, id: "twofa-uri" }, t("twofa.open_app"))),
        code.row,
      ],
      run: async ({ note: cardNote }) => {
        const body = { code: code.input.value.trim() };
        if (status.enabled) body.proof = await proof.collect();
        await post("/api/2fa/enable", body);
        code.input.value = "";
        cardNote.textContent = t("twofa.link_sent");
      },
    });
    card.form.className = "inner";
    replace(appBox, card.form);
  };

  const appBox = h("div", { class: "factor" });
  const drawApp = () =>
    replace(
      appBox,
      h("h3", {}, t("twofa.app_title")),
      status.app
        ? [h("p", { id: "twofa-app-state" }, t("twofa.app_on")), status.keys.length ? button("twofa-remove-app", "twofa.app_remove", () => linked("/api/2fa/remove-app")) : h("p", { class: "hint" }, t("twofa.app_last"))]
        : [h("p", { id: "twofa-app-state" }, t("twofa.app_off")), button("twofa-setup", "twofa.setup", () => attempt(async () => showSetup(await post("/api/2fa/setup")), null), "primary")],
    );
  drawApp();

  const keyName = field("twofa-key-name", t("twofa.key_name"), { maxlength: 40, autocomplete: "off" });
  const addKey = () =>
    attempt(async () => {
      const proofBody = await proof.collect();
      const { options } = await post("/api/2fa/webauthn/options", status.enabled ? { proof: proofBody } : {});
      const credential = await createCredential(shell, options);
      await post("/api/2fa/webauthn/register", { credential, name: keyName.input.value.trim() || null });
      keyName.input.value = "";
    });
  const keysBox = h(
    "div",
    { class: "factor" },
    h("h3", {}, t("twofa.keys_title")),
    status.keys.length
      ? h("ul", { id: "twofa-keys" }, status.keys.map((key) =>
          h("li", {}, h("span", {}, key.last_used ? t("twofa.key_used", { name: key.name, date: when(key.last_used) }) : t("twofa.key_unused", { name: key.name })), " ",
            status.keys.length > 1 || status.app ? button(`key-remove-${key.id}`, "twofa.key_remove", () => linked("/api/2fa/webauthn/remove", { key_id: key.id })) : null)))
      : h("p", { class: "hint" }, t("twofa.keys_none")),
    supported() ? [keyName.row, button("twofa-add-key", "twofa.key_add", addKey)] : h("p", { class: "hint" }, t("key.unsupported")),
  );

  const parts = [h("p", { id: "twofa-state" }, status.enabled ? t("twofa.on", { left: status.recovery_codes_left }) : t("twofa.off"))];
  if (status.enabled) parts.push(h("p", { class: "hint" }, t("twofa.proof_hint")), proof.row);
  parts.push(appBox, keysBox);
  if (status.enabled) {
    parts.push(
      h("div", { class: "factor" }, h("h3", {}, t("twofa.recovery_title")), h("p", { class: "hint" }, t("twofa.recovery_hint")), button("twofa-recovery", "twofa.recovery_submit", () => linked("/api/2fa/recovery-codes"))),
      h("div", { class: "factor", id: "twofa-browsers" },
        h("h3", {}, t("twofa.browsers_title")),
        browsers.length
          ? [h("ul", {}, browsers.map((each) =>
              h("li", {}, h("span", {}, t(each.this ? "twofa.browser_this" : "twofa.browser", { label: each.label, date: when(each.expires) })), " ",
                button(`browser-forget-${each.id}`, "twofa.browser_forget", () => attempt(async () => { await post("/api/2fa/browsers/forget", { id: each.id }); await redraw(); }, null)))),
            ), button("browsers-forget-all", "twofa.browsers_forget_all", () => attempt(async () => { await post("/api/2fa/browsers/forget", {}); await redraw(); }, null))]
          : h("p", { class: "hint" }, t("twofa.browsers_none"))),
      h("div", { class: "factor" }, h("p", { class: "hint" }, t("twofa.disable_hint")), button("twofa-disable", "twofa.disable_submit", () => linked("/api/2fa/disable"))),
    );
  }
  replace(section, h("h2", {}, t("twofa.title")), ...parts, problem, note);
  return section;
}

/** The whole signed-in view. */
export async function accountView(shell, onLogout, redraw) {
  const t = (key, values) => shell.text.get(key, values);
  const status = await get("/api/2fa");
  return h(
    "div",
    { class: "stack" },
    h("p", { id: "who" }, t("account.signed_in", { username: shell.account.username })),
    h("p", {}, h("a", { href: "/profile/", id: "to-profile" }, t("account.profile"))),
    h("div", { class: "cards" }, handleCard(shell), emailCard(shell, status), await signInMethodsCard(shell, status, redraw), await twoFactorCard(shell, status, redraw)),
    h("p", {}, h("button", { type: "button", id: "logout", onclick: onLogout }, t("account.logout"))),
  );
}

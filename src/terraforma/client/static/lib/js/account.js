// The account pages. One page file answers /account/ (log in, register, ask for a password reset, and when logged in the handle,
// email address and two-factor cards) and the pages the emailed links open (/confirm-email, /change-email, /reset-password,
// /confirm-2fa). Which one shows comes from the address, so a link in a mail lands on the right card.

import { h, replace } from "./dom.js";
import { errorText, finishCard, providerButtons, signInMethodsCard, twoFactorStep } from "./external.js";
import { loginForm } from "./login.js";
import { registerForm, resetCompleteForm, resetRequestForm } from "./register.js";
import { get } from "./api.js";
import { messageCard } from "./forms.js";
import { accountView } from "./settings.js";
import { confirmEmail, confirmEmailChange, confirmTwoFactor } from "./tokens.js";
import { Shell } from "./shell.js";

const shell = new Shell("account");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

// A link's token is read once and taken out of the address bar, so it isn't kept in history or copied by accident.
const token = new URLSearchParams(location.search).get("token");
if (token !== null) history.replaceState(null, "", location.pathname + location.hash);

// The game's page sends a logged-out player here with ?next=play (the profile page, ?next=profile; the alliance pages, ?next=alliances); once they are in, they go
// back. Only those names are understood, never an address, so the link can't be used to send a player somewhere else.
const WAYS_BACK = { play: "/play/", profile: "/profile/", alliances: "/alliances/" };
const asked = new URLSearchParams(location.search);
const next = asked.get("next");
// The sign-in provider (oidc/routes.py) sends a player here with ?next=oidc&request=<the site's request, signed by the server>; once they are in, they
// go to /oidc/resume with it, which carries on with the request. It is checked there; the page never follows it as an address.
const oidcRequest = asked.get("request");
if (next === "oidc" && oidcRequest) WAYS_BACK.oidc = `/oidc/resume?request=${encodeURIComponent(oidcRequest)}`;
const backTo = Object.hasOwn(WAYS_BACK, next) ? next : null;
const loginAddress = backTo === "oidc" ? `/account/?next=oidc&request=${encodeURIComponent(oidcRequest)}` : backTo ? `/account/?next=${backTo}` : "/account/";

// A trip to an external sign-in provider ends here with ?external=<outcome> (and ?reason=<word> for an error); read once, then taken out.
const external = new URLSearchParams(location.search).get("external");
const externalReason = new URLSearchParams(location.search).get("reason");
if (external !== null) history.replaceState(null, "", location.pathname + (backTo ? `?next=${backTo}` : "") + location.hash);

const link = (href, textKey, id) => h("a", { href, id }, t(textKey));
const toLogin = () => h("p", {}, link(loginAddress, "account.log_in", "to-login"));

function title(key) {
  document.title = `${t(key)} · ${shell.game.game ?? "TerraForma"}`;
}

function show(titleKey, ...children) {
  title(titleKey);
  replace(main, h("div", { class: "stack" }, ...children));
}

/** Logging in, with the ways to a new account and a new password beside it. */
async function showLogin(message = null) {
  show(
    "login.title",
    message,
    loginForm(shell, async (login) => {
      shell.signedIn(login);
      if (backTo) return location.assign(WAYS_BACK[backTo]);
      await showAccount();
    }),
    await providerButtons(shell, backTo),
    h("p", { class: "links" }, link("#register", "login.register", "to-register"), " · ", link("#reset", "login.forgot", "to-reset")),
  );
}

async function showAccount(message = null) {
  await shell.restore(); // the handle comes with the session, not with the login
  title("account.title");
  replace(
    main,
    message,
    await accountView(shell, async () => {
      await shell.logout();
      showLogin();
    }, () => showAccount()),
  );
}

/** Where a trip to an external sign-in provider ended up (the server sent the browser back with the outcome in the address). */
async function showExternal() {
  const done = async (login) => {
    shell.signedIn(login);
    const target = Object.hasOwn(WAYS_BACK, login.next) ? login.next : backTo;
    if (target) return location.assign(WAYS_BACK[target]);
    await showAccount();
  };
  switch (external) {
    case "done":
      if (await shell.restore()) return backTo ? location.replace(WAYS_BACK[backTo]) : showAccount();
      return showLogin(h("p", { class: "problem", role: "alert" }, errorText(shell, "expired")));
    case "twofa":
      return show("external.twofa_title", twoFactorStep(shell, done), toLogin());
    case "finish": {
      const { pending } = await get("/api/external/pending");
      if (!pending) return showLogin();
      return show("external.finish_title", finishCard(shell, pending, done), toLogin());
    }
    case "linked":
      return showAccount(h("p", { class: "note", role: "status", id: "external-note" }, t("external.linked")));
    default: {
      const problem = h("p", { class: "problem", role: "alert", id: "external-problem" }, errorText(shell, externalReason));
      return (await shell.restore()) ? showAccount(problem) : showLogin(problem);
    }
  }
}

function showRegister() {
  show("register.title", registerForm(shell, (sent) => show("register.sent_title", sent)), toLogin());
}

function showReset() {
  show("reset.title", resetRequestForm(shell, (sent) => show("reset.sent_title", sent)), toLogin());
}

function missingToken() {
  show("confirm.missing_title", messageCard(shell, "confirm.missing_title", "confirm.missing"), toLogin());
}

/** The page of one emailed link: $card draws the button, and $finished the card for what came of it. */
function tokenPage(titleKey, makeCard, finished) {
  if (!token) return missingToken();
  show(titleKey, makeCard(shell, token, (answer) => show(titleKey, finished(answer), toLogin())), toLogin());
}

function recoveryCodes(codes) {
  if (!codes) return null;
  return h(
    "div",
    { class: "card" },
    h("h2", {}, t("confirm.codes_title")),
    h("p", {}, t("confirm.codes_hint")),
    h("ul", { id: "recovery-codes", class: "codes" }, codes.map((code) => h("li", {}, h("code", {}, code)))),
  );
}

/** The two-factor link needs the account it was made for, so it asks for a login first. */
async function twoFactorPage() {
  if (!token) return missingToken();
  const draw = () =>
    show(
      "confirm.twofa_title",
      confirmTwoFactor(shell, token, (answer) =>
        show("confirm.twofa_title", messageCard(shell, "confirm.twofa_title", answer.enabled ? "confirm.twofa_on" : "confirm.twofa_off"), recoveryCodes(answer.recovery_codes), h("p", {}, link("/account/", "account.title", "to-account"))),
      ),
    );
  if (await shell.restore()) return draw();
  show(
    "login.title",
    h("p", { class: "card" }, t("confirm.twofa_login")),
    loginForm(shell, (login) => {
      shell.signedIn(login);
      draw();
    }),
  );
}

async function route() {
  switch (location.pathname) {
    case "/confirm-email":
      return tokenPage("confirm.email_title", confirmEmail, (answer) => messageCard(shell, "confirm.email_title", "confirm.email_done", { username: answer.username }));
    case "/change-email":
      return tokenPage("confirm.change_title", confirmEmailChange, () => messageCard(shell, "confirm.change_title", "confirm.change_done"));
    case "/reset-password":
      if (!token) return missingToken();
      return show("reset.complete_title", resetCompleteForm(shell, token, () => show("reset.complete_title", messageCard(shell, "reset.complete_title", "reset.done"), toLogin())), toLogin());
    case "/confirm-2fa":
      return twoFactorPage();
    default:
      if (external !== null) return showExternal();
      if (await shell.restore()) return backTo ? location.replace(WAYS_BACK[backTo]) : showAccount();
      if (location.hash === "#register") return showRegister();
      if (location.hash === "#reset") return showReset();
      return showLogin();
  }
}

await shell.start();
await route();
if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));

// The links between the login, registration and reset cards only change the address's hash.
window.addEventListener("hashchange", () => {
  if (location.pathname === "/account/" && !shell.account) route();
});

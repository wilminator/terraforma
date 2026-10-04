// The account page: log in, see who you are, log out. Registration, 2FA and the rest come with the account pages.

import { h, replace } from "./dom.js";
import { loginForm } from "./login.js";
import { Shell } from "./shell.js";

const shell = new Shell("account");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

function showLogin() {
  replace(main, loginForm(shell, (login) => { shell.signedIn(login); showAccount(); }));
}

function showAccount() {
  replace(
    main,
    h(
      "section",
      { class: "card" },
      h("p", { id: "who" }, t("account.signed_in", { username: shell.account.username })),
      h("button", { type: "button", id: "logout", onclick: async () => { await shell.logout(); showLogin(); } }, t("account.logout")),
    ),
  );
}

await shell.start();
if (await shell.restore()) showAccount();
else showLogin();

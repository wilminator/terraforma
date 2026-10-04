// The game client's page: log in, then the stage and its panels.

import { h, replace } from "./dom.js";
import { loginForm } from "./login.js";
import { Shell } from "./shell.js";
import { fitStage } from "./stage.js";

const shell = new Shell("play");
const app = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);
let stopFitting = null;

function showLogin() {
  stopFitting?.();
  replace(app, h("div", { class: "centered" }, loginForm(shell, (login) => { shell.signedIn(login); showGame(); })));
}

function showGame() {
  const canvas = h("div", { class: "stage-canvas", id: "stage-canvas" });
  const stage = h("div", { class: "stage", id: "stage", role: "img", "aria-label": t("play.stage") }, canvas);
  const frame = h("div", { class: "stage-frame" }, stage);
  const logout = h("button", { type: "button", id: "logout", onclick: async () => { await shell.logout(); showLogin(); } }, t("play.logout"));
  replace(
    app,
    h("div", { class: "game" }, frame, h("header", { class: "bar" }, h("span", { id: "who" }, t("play.signed_in", { username: shell.account.username })), logout)),
  );
  stopFitting = fitStage(frame, stage, canvas);
}

async function main() {
  await shell.start();
  document.title = shell.game.game ?? "TerraForma";
  if (await shell.restore()) showGame();
  else showLogin();
  if (shell.problems.length) app.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

main();

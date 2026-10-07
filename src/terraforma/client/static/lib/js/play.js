// The game client's page: log in, then the stage and its panels.

import { h, replace } from "./dom.js";
import { problemText } from "./forms.js";
import { mountPanels } from "./panels.js";
import { Roster } from "./roster.js";
import { Shell } from "./shell.js";
import { fitStage } from "./stage.js";

const shell = new Shell("play");
const app = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

/** There is one login, on the account page (it works in any orientation); it sends the player back here once they are in. */
function toLogin() {
  location.replace("/account/?next=play");
}

async function showGame() {
  const canvas = h("div", { class: "stage-canvas", id: "stage-canvas" });
  const stage = h("div", { class: "stage", id: "stage", role: "img", "aria-label": t("play.stage") }, canvas);
  const frame = h("div", { class: "stage-frame" }, stage);
  const main = h("div", { class: "main" }, frame);
  const tabs = h("div", { class: "tabs-host" });
  const problem = h("p", { class: "problem", role: "alert" });
  const logout = h("button", { type: "button", id: "logout", onclick: async () => { await shell.logout(); toLogin(); } }, t("play.logout"));
  replace(
    app,
    h("div", { class: "game" }, main, h("header", { class: "bar" }, h("span", { id: "who" }, t("play.signed_in", { username: shell.account.username })), tabs, problem, logout)),
  );
  fitStage(frame, stage, canvas);

  const roster = new Roster();
  try {
    await roster.load();
  } catch (error) {
    problem.textContent = problemText(shell, error);
    return;
  }
  const { buttons, drawer } = mountPanels(shell, roster, roster.teams.length ? null : "teams");
  replace(tabs, buttons);
  main.append(drawer);
  shell.emit("game", { roster });
}

async function main() {
  await shell.start();
  document.title = shell.game.game ?? "TerraForma";
  if (!(await shell.restore())) return toLogin();
  showGame();
  if (shell.problems.length) app.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

main();

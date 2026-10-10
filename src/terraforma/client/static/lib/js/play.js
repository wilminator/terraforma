// The game client's page, the one page of the game: the stage (the map, later the fight) and the panels over it. Nothing in the
// game loads another page. A player who is not logged in is sent to the account page and comes back.

import { h, replace } from "./dom.js";
import { problemText } from "./forms.js";
import { MapScene } from "./map-scene.js";
import { mountPanels } from "./panels.js";
import { Roster } from "./roster.js";
import { Shell } from "./shell.js";
import { DESIGN_HEIGHT, DESIGN_WIDTH, fitStage } from "./stage.js";

const shell = new Shell("play");
const app = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

/** There is one login, on the account page (it works in any orientation); it sends the player back here once they are in. */
function toLogin() {
  location.replace("/account/?next=play");
}

async function showGame() {
  // The stage is the one picture of the game: the map now, the fight later, both drawn on this canvas at the design resolution.
  const view = h("canvas", { class: "stage-view", id: "stage-view", width: String(DESIGN_WIDTH), height: String(DESIGN_HEIGHT) });
  const canvas = h("div", { class: "stage-canvas", id: "stage-canvas" }, view);
  const stage = h("div", { class: "stage", id: "stage", role: "application", tabindex: "0", "aria-label": t("play.stage") }, canvas);
  const note = h("p", { class: "stage-note", id: "stage-note", role: "status" });
  const frame = h("div", { class: "stage-frame" }, stage, note);
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
  new MapScene(shell, roster, { stage, canvas: view, note });
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

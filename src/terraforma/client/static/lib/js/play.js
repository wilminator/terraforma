// The game client's page, the one page of the game: the stage (the map, or a fight) and the panels over it. Nothing in the
// game loads another page. A player who is not logged in is sent to the account page and comes back.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { FightScene } from "./fight-scene.js";
import { problemText } from "./forms.js";
import { MapScene } from "./map-scene.js";
import { mountPanels } from "./panels.js";
import { Roster } from "./roster.js";
import { Shell } from "./shell.js";
import { settleHeroes } from "./disuse.js";
import { DESIGN_HEIGHT, DESIGN_WIDTH, fitStage } from "./stage.js";

const shell = new Shell("play");
const app = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

/** There is one login, on the account page (it works in any orientation); it sends the player back here once they are in. */
function toLogin() {
  location.replace("/account/?next=play");
}

async function showGame() {
  // The stage is the one picture of the game, at the design resolution: the map is drawn on this canvas, a fight in a layer over it.
  const view = h("canvas", { class: "stage-view", id: "stage-view", width: String(DESIGN_WIDTH), height: String(DESIGN_HEIGHT) });
  const canvas = h("div", { class: "stage-canvas", id: "stage-canvas" }, view);
  const stage = h("div", { class: "stage", id: "stage", role: "application", tabindex: "0", "aria-label": t("play.stage") }, canvas);
  const note = h("p", { class: "stage-note", id: "stage-note", role: "status" });
  const frame = h("div", { class: "stage-frame" }, stage, note);
  const main = h("div", { class: "main" }, frame);
  const tabs = h("div", { class: "tabs-host" });
  const problem = h("p", { class: "problem", role: "alert" });
  const logout = h("button", { type: "button", id: "logout", onclick: async () => { await shell.logout(); toLogin(); } }, t("play.logout"));
  // The sound button shows only when the game gave the page some sound; its words say what the sound is now, and a tap turns it the other way.
  const sound = h("button", { type: "button", id: "sound", onclick: () => shell.audio.set({ muted: !shell.audio.muted }) });
  const drawSound = () => {
    sound.hidden = !shell.audio.available;
    sound.textContent = t(shell.audio.muted ? "play.sound_off" : "play.sound_on");
  };
  shell.audio.onChange(drawSound);
  drawSound();
  replace(
    app,
    h("div", { class: "game" }, main, h("header", { class: "bar" }, h("span", { id: "who" }, t("play.signed_in", { username: shell.account.username })), tabs, problem, sound, logout)),
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
  const map = new MapScene(shell, roster, { stage, canvas: view, note });
  const fight = new FightScene(shell, { stage, host: canvas });
  stage.dataset.scene = "map";

  // The fight is another scene of the same stage: the map lets go of it while a fight is shown, and takes it back when the player leaves.
  async function enterFight(id) {
    if (!(await fight.enter(id, (text) => (note.textContent = text)))) return;
    map.suspend();
    shell.audio.music("fight");
    shell.panels.close(); // (a panel over the stage's right side would cover half the fight)
    view.style.display = "none";
    stage.dataset.scene = "fight";
    stage.focus();
  }
  async function leaveFight() {
    fight.close();
    view.style.display = "";
    stage.dataset.scene = "map";
    shell.audio.music("map");
    await roster.load(); // (a fight pays out: levels, gold and drops)
    await map.resume();
  }
  shell.on("fight", ({ fight: id }) => enterFight(id));
  shell.audio.music("map");
  stage.addEventListener("fight-leave", leaveFight);
  shell.emit("game", { roster });

  // A page that opens in the middle of a fight (a reload) goes straight back to it.
  try {
    const [running] = await api.get("/api/fights?running=true&limit=1");
    if (running) await enterFight(running.id);
    // A hero that holds something the game has retired (or has something kept that can come back) goes through its scene first.
    await settleHeroes(shell, roster, document.querySelector(".game"));
  } catch (error) {
    problem.textContent = problemText(shell, error);
  }
}

async function main() {
  await shell.start();
  document.title = shell.game.game ?? "TerraForma";
  if (!(await shell.restore())) return toLogin();
  showGame();
  if (shell.problems.length) app.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

main();

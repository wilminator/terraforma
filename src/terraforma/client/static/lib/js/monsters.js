// The monsters page: a person who is not logged in queues to command the monsters of a fight, and then commands them. There is no
// account: the server makes a guest and answers with a token once, which this page keeps in the browser and sends with every call
// (see monsterplay/routes.py, "guests"). The queue is a card that asks the server every so often (the check-in that keeps the place);
// when a fight seats the guest, the page shows that fight with the same scene the game uses, fed by the guest's calls and socket.
// The server's rules decide whether a game takes guests at all: where it doesn't, joining answers with its reason.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { FightScene } from "./fight-scene.js";
import { guestSource } from "./fight-source.js";
import { problemText } from "./forms.js";
import { Shell } from "./shell.js";
import { DESIGN_HEIGHT, DESIGN_WIDTH, fitStage } from "./stage.js";

const shell = new Shell("monsters");
const app = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);

const KEY = "terraforma.monster-guest";
const remembered = {
  get() {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null; // (storage may be blocked: the guest then lasts as long as the page)
    }
  },
  set(token) {
    try {
      localStorage.setItem(KEY, token);
    } catch {
      // (see above)
    }
  },
  clear() {
    try {
      localStorage.removeItem(KEY);
    } catch {
      // (see above)
    }
  },
};

let token = remembered.get();
let timer = null;
let run = 0; // changes whenever the page moves on, so an answer that arrives late is dropped
let scene = null;

const problem = h("p", { class: "problem", role: "alert", id: "monsters-problem" });
const holder = h("div", { class: "main", id: "monsters-main" });

/** A call as the guest: its token is the only proof of who it is. */
const guest = (method, path) => api.call(method, `/api/monsters/guest${path}`, method === "GET" ? undefined : {}, { Authorization: `Bearer ${token}` });

function card(state, ...children) {
  holder.dataset.state = state;
  replace(holder, h("div", { class: "centered" }, h("section", { class: "card", id: "monsters-card" }, h("h1", {}, t("monsters.title")), ...children)));
}

function offer(note) {
  const button = h("button", { type: "button", id: "monsters-join", class: "primary", onclick: () => join(button) }, t("monsters.join"));
  card("join", h("p", {}, t("monsters.intro")), note ? h("p", { id: "monsters-note", role: "status" }, note) : null, button);
}

function waiting(status) {
  card(
    "queued",
    h("p", { id: "monsters-place", role: "status" }, t("monsters.waiting", { position: status.position })),
    h("button", { type: "button", id: "monsters-leave", onclick: () => leave() }, t("monsters.leave")),
  );
}

function forget() {
  token = null;
  remembered.clear();
}

/** Shows what the server says the guest is doing: commanding a fight, waiting in the queue, or neither (and why it can't, if it can't). */
function show(status) {
  clearTimeout(timer);
  const mine = ++run;
  problem.textContent = "";
  if (status.seat) return enterFight(status.seat.fight, mine);
  if (!status.queued) return offer(status.refusal);
  waiting(status);
  timer = setTimeout(() => refresh(mine), Math.max(1, status.checkin_seconds / 3) * 1000);
}

/** Asks where the guest stands and, if it waits, says it is still there (which keeps its place); shows what that answers. */
async function refresh(mine) {
  if (mine !== run) return;
  let status;
  try {
    status = await guest("GET", "");
    if (status.queued) status = await guest("POST", "/check-in");
  } catch (error) {
    if (mine !== run) return;
    if (error.status === 401) {
      forget();
      offer(t("monsters.lapsed"));
      return;
    }
    problem.textContent = problemText(shell, error);
    timer = setTimeout(() => refresh(mine), 10000);
    return;
  }
  if (mine === run) show(status);
}

async function join(button) {
  button.disabled = true;
  try {
    const answer = await api.call("POST", "/api/monsters/guest/join", {});
    token = answer.token;
    remembered.set(token);
    show(answer);
  } catch (error) {
    problem.textContent = problemText(shell, error);
    button.disabled = false;
  }
}

async function leave() {
  try {
    show(await guest("POST", "/leave"));
  } catch (error) {
    problem.textContent = problemText(shell, error);
  }
}

async function enterFight(id, mine) {
  const canvas = h("div", { class: "stage-canvas", id: "stage-canvas" });
  const stage = h("div", { class: "stage", id: "stage", role: "application", tabindex: "0", "aria-label": t("monsters.stage") }, canvas);
  const note = h("p", { class: "stage-note", id: "stage-note", role: "status" });
  const frame = h("div", { class: "stage-frame" }, stage, note);
  holder.dataset.state = "fight";
  replace(holder, frame);
  fitStage(frame, stage, canvas);
  scene = new FightScene(shell, { stage, host: canvas, source: guestSource(token) });
  stage.dataset.scene = "fight";
  stage.addEventListener("fight-leave", () => {
    scene.close();
    scene = null;
    refresh(++run);
  });
  if (!(await scene.enter(id, (text) => (note.textContent = text))) && mine === run) {
    scene = null;
    problem.textContent = note.textContent;
    timer = setTimeout(() => refresh(mine), 5000); // (the fight may not be ready to show yet: ask again)
    return;
  }
  stage.focus();
}

async function main() {
  await shell.start();
  document.title = shell.game.game ? `${t("monsters.title")} · ${shell.game.game}` : t("monsters.title");
  replace(app, h("div", { class: "game" }, holder, h("header", { class: "bar" }, h("span", { id: "who" }, t("monsters.title")), problem)));
  if (shell.problems.length) problem.textContent = shell.problems.join(" ");
  if (token) await refresh(run);
  else offer(null);
}

main();

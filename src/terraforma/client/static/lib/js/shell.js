// The page's shell: what a game's browser module gets. A game module is an ES module whose default export is called
// once, with the shell, before the page draws:
//
//   export default function register(shell) {
//     shell.text.set({ "login.title": "Enter the tavern" });
//     shell.on("login", ({ username }) => { ... });
//   }
//
// shell.page      "site", "account" or "play"
// shell.game      what GET /api/client said: { engine, game, modules, styles, assets }
// shell.assetUrl(name)   the URL of one of the game's assets
// shell.art.load(reference)   a picture from the game's assets: a file name or a sheet reference (see art.js)
// shell.text      the page's words (get, set); an element with data-text="key" shows that text
// shell.api       { get, post, call, ApiError }
// shell.account   null, or { username, handle } once logged in
// shell.on(event, handler)   "ready" (every module registered, nothing drawn yet), "login", "logout", and on the play page "game"
//                            (the panels are drawn; the detail is { roster }); returns a function that stops listening
// shell.panels    the game client's panels: the engine's (teams, party, nearby, talk), and shell.panels.add(id, build) for a game's own
// shell.actions   the actions the nearby list can ask for (talk, invite, open, search, fight, help); a game pushes the names it adds
// shell.activities  what the talk panel shows when an NPC's dialog reaches an activity: the engine's shop, and shell.activities.add(command, build)
// shell.conversation  the conversation the hero is in (lines said, the prompt); the talk panel draws it

import { Activities } from "./activities.js";
import * as api from "./api.js";
import { Art } from "./art.js";
import { Conversation } from "./conversation.js";
import { Panels } from "./panels.js";
import { Text } from "./strings.js";

export class Shell {
  page;
  game = null;
  account = null;
  text = new Text();
  art = new Art((name) => this.assetUrl(name));
  api = api;
  panels = new Panels();
  activities = new Activities();
  conversation = new Conversation();
  actions = ["talk", "invite", "open", "search", "fight", "help"];
  problems = [];
  #handlers = new Map();

  constructor(page) {
    this.page = page;
    this.panels.addEngine();
    this.activities.addEngine();
  }

  on(event, handler) {
    const handlers = this.#handlers.get(event) ?? new Set();
    handlers.add(handler);
    this.#handlers.set(event, handlers);
    return () => handlers.delete(handler);
  }

  emit(event, detail) {
    for (const handler of this.#handlers.get(event) ?? []) {
      try {
        handler(detail);
      } catch (error) {
        console.error(`A game handler for "${event}" failed:`, error);
      }
    }
  }

  assetUrl(name) {
    return `${this.game?.assets ?? "/assets/"}${name}`;
  }

  /** Asks the server what the game adds, loads its styles, then its modules in order. A module that fails is reported and skipped. */
  async start() {
    this.game = await api.get("/api/client");
    document.querySelectorAll("[data-game-name]").forEach((element) => {
      element.textContent = this.game.game ?? "TerraForma";
    });
    for (const href of this.game.styles) {
      document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href }));
    }
    for (const url of this.game.modules) {
      try {
        const module = await import(url);
        await module.default?.(this);
      } catch (error) {
        console.error(`Could not load ${url}:`, error);
        this.problems.push(this.text.get("problem.module", { file: url.split("/").pop() }));
      }
    }
    this.emit("ready");
    document.querySelectorAll("[data-text]").forEach((element) => {
      element.textContent = this.text.get(element.dataset.text);
    });
  }

  /** Looks for a login that is still good (a reload keeps the cookie, not the CSRF token); returns the account or null. */
  async restore() {
    const session = await api.get("/api/session");
    api.setCsrfToken(session.csrf_token);
    this.account = session.account;
    return this.account;
  }

  signedIn(login) {
    api.setCsrfToken(login.csrf_token);
    this.account = { username: login.username, handle: null };
    this.emit("login", this.account);
  }

  async logout() {
    await api.post("/api/logout");
    api.setCsrfToken(null);
    this.account = null;
    this.emit("logout");
  }
}

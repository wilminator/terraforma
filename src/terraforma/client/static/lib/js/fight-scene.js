// The fight scene of the stage: the fighters of both sides drawn on the stage's design resolution, and the server's rounds played back
// as they arrive over the fight socket, one event at a time (what is said, who moves, how much each gauge falls), at a pace the player
// can speed up or skip. The server decides everything; this only shows the events it stored. The screen is built from the fight's view
// (who stands, with how much) and kept up to date by the same events, so a page that opens in the middle of a fight draws it as it is.
//
// The motion uses the Web Animations API and waits for each animation to finish before the next event, so the pace is the pacing
// of the events and nothing is timed against the clock. A player who asks the system for less motion sees the same text and gauges, and
// no movement.

import * as api from "./api.js";
import { drawFrame, frameRect } from "./art.js";
import { h, replace } from "./dom.js";
import { FightLink } from "./fight-link.js";
import { FightMenu } from "./fight-menu.js";
import { FightModel } from "./fight-model.js";

const SPEEDS = [1, 2, 4];
const READ_MS = 650; // how long an event stays before the next at normal speed
const MOVE_MS = 260; // how long one movement takes at normal speed
const SPRITE = 48; // the size of a fighter's picture on the card (the sprite sheet's frame is scaled to it)

const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

export class FightScene {
  /** $stage is the element that carries the data attributes; $host the element the scene's layer goes in. */
  constructor(shell, { stage, host }) {
    this.shell = shell;
    this.stage = stage;
    this.host = host;
    this.t = (key, values) => shell.text.get(key, values);
    this.layer = null;
    this.model = null;
    this.link = null;
    this.cards = new Map();
    this.menu = new FightMenu(this);
    this.queue = [];
    this.playing = false;
    this.skipping = false;
    this.speed = 1;
    this.shown = 0; // how many events have been played since the scene opened
    this.run = 0; // changes when the scene closes, so a playback in progress stops
    this.moving = new Set(); // the animations in flight
    this.sprites = new Map(); // fighter key -> { canvas, loaded, frame, player } for each fighter whose personality has fight art
    this.stopSprites = () => {};
    this.linkState = "closed";
    this.opened = 0;
  }

  get active() {
    return this.model !== null;
  }

  /** Opens the scene on fight $id. Returns false (and says why in $note) if the fight can't be shown. */
  async enter(id, note) {
    this.close();
    let view;
    try {
      view = await api.get(`/api/fights/${id}`);
    } catch (error) {
      note?.(error.message);
      return false;
    }
    const run = ++this.run;
    this.model = new FightModel(view);
    this.queue = [];
    this.shown = 0;
    this.speed = 1;
    this.skipping = false;
    this.#build();
    this.#data();
    this.link = new FightLink(id, {
      message: (message) => this.#message(run, message),
      state: (state) => this.#linkChanged(run, state),
    });
    this.link.start();
    return true;
  }

  /** Leaves the scene (the fight goes on without the page). */
  close() {
    this.run += 1;
    this.link?.stop();
    this.link = null;
    for (const each of this.moving) each.cancel();
    this.moving.clear();
    this.menu.detach();
    this.#clearSprites();
    this.layer?.remove();
    this.layer = null;
    this.model = null;
    this.cards = new Map();
    this.queue = [];
    this.playing = false;
    this.#data();
  }

  // --- the line to the server ----------------------------------------------------------------------

  #message(run, message) {
    if (run !== this.run || !this.model) return;
    if (message.type === "round") {
      this.queue.push(message);
      this.#pump();
    } else if (message.type === "committed") {
      const who = this.model.fighter(`${message.fighter.party}.${message.fighter.group}.${message.fighter.character}`);
      if (who && message.round === this.model.round) who.committed = true;
      this.#paint();
    } else if (message.type === "party_joined") {
      this.#resync(run);
    }
  }

  async #linkChanged(run, state) {
    if (run !== this.run) return;
    this.linkState = state;
    this.#data();
    if (state === "open") {
      this.opened += 1;
      if (this.opened > 1) await this.#resync(run); // (the socket dropped and came back: rounds may have played meanwhile)
    }
  }

  /** Asks the server how the fight stands, and shows that if nothing is being played. */
  async #resync(run) {
    if (this.playing || this.queue.length) return;
    try {
      const view = await api.get(`/api/fights/${this.model.id}`);
      if (run !== this.run || this.playing || this.queue.length) return;
      this.model = new FightModel(view);
      this.#build();
      this.#data();
    } catch {
      // (the next message tries again)
    }
  }

  // --- playback ------------------------------------------------------------------------------------

  async #pump() {
    if (this.playing) return;
    const run = this.run;
    this.playing = true;
    this.#data();
    while (this.queue.length && run === this.run) {
      const round = this.queue.shift();
      for (const event of round.events) {
        if (run !== this.run) return;
        const result = this.model.apply(event);
        this.shown += 1;
        if (result.text) this.#say(...result.text);
        this.#paint();
        await this.#play(run, result.marks);
        if (result.reload) {
          this.playing = false;
          await this.#resync(run);
          this.playing = true;
        }
      }
      if (run !== this.run) return;
      this.model.endRound(round.round, round.over, round.deadline);
      this.#paint();
      // Playing the round out counts as play time (up to the game's cap since it played); a failure here is not the player's concern.
      api.post(`/api/fights/${this.model.id}/playback-done`, { round: round.round }).catch(() => {});
    }
    if (run !== this.run) return;
    if (!this.model.over) await this.refreshMenu(run);
    if (run !== this.run) return;
    this.playing = false;
    this.skipping = false;
    this.#data();
    if (this.model.over) this.#finish();
  }

  /** The motion and the pause that go with one event. */
  async #play(run, marks) {
    if (this.skipping) return;
    const motion = !reducedMotion();
    await Promise.all(marks.map((mark) => (motion ? this.#move(mark) : null)));
    if (run === this.run && !this.skipping) await this.#wait(READ_MS / this.speed);
  }

  #wait(ms) {
    return new Promise((done) => {
      this.waiting = done;
      setTimeout(done, ms);
    });
  }

  async #move(mark) {
    const card = this.cards.get(mark.key);
    if (!card) return;
    const ms = MOVE_MS / this.speed;
    const toward = card.dataset.side === "left" ? 1 : -1;
    const run = (element, keyframes, options) => {
      const animation = element.animate(keyframes, { duration: ms, easing: "ease-out", ...options });
      this.moving.add(animation);
      return animation.finished.catch(() => {}).finally(() => this.moving.delete(animation));
    };
    if (mark.kind === "act" || mark.kind === "turn") {
      await run(card, [{ transform: "translateX(0)" }, { transform: `translateX(${toward * 10}px)` }, { transform: "translateX(0)" }]);
    } else if (mark.kind === "damage" || mark.kind === "heal" || mark.kind === "miss") {
      const text = mark.kind === "miss" ? this.t("fight.miss_float") : `${mark.kind === "damage" ? "−" : "+"}${mark.amount}`;
      const floating = h("span", { class: `fight-float ${mark.kind}`, "aria-hidden": "true" }, text);
      card.append(floating);
      await run(floating, [{ transform: "translateY(0)", opacity: 1 }, { transform: "translateY(-18px)", opacity: 0 }], { duration: ms * 2, fill: "forwards" });
      floating.remove();
    } else if (mark.kind === "died" || mark.kind === "fled") {
      await run(card, [{ opacity: 1 }, { opacity: 0.35 }], { fill: "forwards" });
    }
  }

  /** Asks the server what the player's heroes can do now and who has chosen (the menu's choices change as items and MP are spent). */
  async refreshMenu(run = this.run) {
    try {
      const view = await api.get(`/api/fights/${this.model.id}`);
      if (run === this.run && this.model) {
        this.model.refresh(view);
        this.#paint();
      }
    } catch {
      // (the menu keeps what it had; the next round asks again)
    }
  }

  /** Draws the fight again from the model (the menu calls it when it changes something). */
  repaint() {
    this.#paint();
  }

  skip() {
    this.skipping = true;
    for (const each of this.moving) each.finish();
    this.waiting?.();
    this.#data();
  }

  cycleSpeed() {
    this.speed = SPEEDS[(SPEEDS.indexOf(this.speed) + 1) % SPEEDS.length];
    this.#data();
    this.#paint();
  }

  #finish() {
    this.#paint();
    this.#data();
    this.stage.dispatchEvent(new CustomEvent("fight-over", { detail: { id: this.model.id } }));
  }

  // --- drawing -------------------------------------------------------------------------------------

  #side(fighter) {
    return fighter.party === 0 || fighter.yours ? "left" : "right";
  }

  #build() {
    this.#clearSprites();
    this.layer?.remove();
    this.cards = new Map();
    const sides = { left: h("div", { class: "fight-side left" }), right: h("div", { class: "fight-side right" }) };
    for (const [key, fighter] of this.model.fighters) {
      const card = h(
        "div",
        { class: "fighter", "data-fighter": key, "data-side": this.#side(fighter) },
        fighter.art ? this.#spot(key) : null,
        h("span", { class: "fighter-name" }, fighter.name),
        h("span", { class: "fighter-bar" }, h("span", { class: "fighter-fill" })),
        h("span", { class: "fighter-hp" }),
        h("span", { class: "fighter-mark", "aria-hidden": "true" }),
      );
      this.cards.set(key, card);
      sides[this.#side(fighter)].append(card);
    }
    this.statusLine = h("span", { class: "fight-round", id: "fight-round", role: "status" });
    this.skipButton = h("button", { type: "button", id: "fight-skip", onclick: () => this.skip() }, this.t("fight.skip"));
    this.speedButton = h("button", { type: "button", id: "fight-speed", onclick: () => this.cycleSpeed() });
    this.leaveButton = h("button", { type: "button", id: "fight-leave", class: "primary", hidden: true, onclick: () => this.stage.dispatchEvent(new CustomEvent("fight-leave")) }, this.t("fight.leave"));
    this.lines = h("ol", { class: "fight-log", id: "fight-log", role: "log", "aria-label": this.t("fight.log") });
    this.layer = h(
      "div",
      { class: "fight-layer", id: "fight-layer", role: "group", "aria-label": this.t("fight.label") },
      h("div", { class: "fight-bar" }, this.statusLine, this.skipButton, this.speedButton, this.leaveButton),
      h("div", { class: "fight-field" }, sides.left, sides.right),
      this.lines,
    );
    this.host.append(this.layer);
    this.menu.attach(this.layer);
    this.#paint();
    this.#picture(this.run);
  }

  // --- pictures ------------------------------------------------------------------------------------

  /** The place on a card where a fighter's picture goes once it has loaded (a fighter whose picture fails to load stays a plain card). */
  #spot(key) {
    const canvas = h("canvas", { class: "fighter-sprite", width: SPRITE, height: SPRITE, "aria-hidden": "true", hidden: true });
    this.sprites.set(key, { canvas, loaded: null, frame: 0, player: null });
    return canvas;
  }

  #clearSprites() {
    this.stopSprites();
    this.stopSprites = () => {};
    this.sprites = new Map();
  }

  /** Loads the fight art of every fighter that has some and draws it; a fighter's art that plays "base" is redrawn whenever the clock shows
   * another frame (one timer serves them all, and none runs for a player who asks for less motion). */
  async #picture(run) {
    const entries = [...this.sprites];
    await Promise.all(
      entries.map(async ([key, sprite]) => {
        const art = this.model.fighter(key)?.art;
        const loaded = await this.shell.art.load(art).catch(() => null);
        if (!loaded?.source || run !== this.run || this.sprites.get(key) !== sprite) return;
        sprite.loaded = loaded;
        sprite.frame = typeof art === "object" ? (art.frame ?? 0) : 0;
        sprite.player = loaded.animations && art.animations && !reducedMotion() ? loaded.animations.play("base") : null;
        sprite.canvas.hidden = false;
        this.#drawSprite(sprite);
      }),
    );
    if (run !== this.run) return;
    const playing = [...this.sprites.values()].filter((each) => each.player);
    this.stopSprites = playing.length
      ? this.shell.art.clock.subscribe(() => {
          for (const each of playing) {
            const at = each.player.frameAt().index;
            if (at !== each.shown) this.#drawSprite(each);
          }
        })
      : () => {};
    this.#data();
  }

  #drawSprite(sprite) {
    const { canvas, loaded } = sprite;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (sprite.player) {
      const shows = sprite.player.frameAt();
      sprite.shown = shows.index;
      drawFrame(context, loaded, shows.frame, 0, 0, SPRITE, SPRITE, shows.ops);
    } else if (loaded.frame) {
      const rect = frameRect(loaded, sprite.frame);
      context.drawImage(loaded.source, rect.x, rect.y, rect.w, rect.h, 0, 0, SPRITE, SPRITE);
    } else {
      context.drawImage(loaded.source, 0, 0, SPRITE, SPRITE);
    }
  }

  #say(key, values) {
    this.lines.append(h("li", {}, this.t(key, values)));
    this.lines.scrollTop = this.lines.scrollHeight;
  }

  #paint() {
    if (!this.layer) return;
    for (const [key, card] of this.cards) {
      const fighter = this.model.fighter(key);
      const [now, most] = this.model.vitalOf(fighter);
      card.dataset.hp = String(now);
      card.dataset.hpMax = String(most);
      card.dataset.alive = String(fighter.alive && !fighter.fled);
      card.dataset.fled = String(fighter.fled);
      card.dataset.yours = String(fighter.yours);
      card.dataset.committed = String(fighter.committed);
      card.querySelector(".fighter-fill").style.width = `${most ? Math.round((now / most) * 100) : 0}%`;
      card.querySelector(".fighter-hp").textContent = `${now}/${most}`;
      card.querySelector(".fighter-mark").textContent = fighter.fled ? "»" : fighter.alive ? (fighter.committed ? "✓" : "") : "✕";
      card.style.opacity = fighter.alive && !fighter.fled ? "" : "0.35";
    }
    this.statusLine.textContent = this.model.over ? this.t("fight.finished") : this.t("fight.round", { round: this.model.round });
    this.speedButton.textContent = this.t("fight.speed", { speed: this.speed });
    this.skipButton.disabled = !this.playing;
    this.leaveButton.hidden = !(this.model.over && !this.playing);
    this.menu.sync();
  }

  #data() {
    const set = (name, value) => (value === null ? delete this.stage.dataset[name] : (this.stage.dataset[name] = String(value)));
    set("fight", this.model?.id ?? null);
    set("link", this.model ? this.linkState : null);
    set("round", this.model?.round ?? null);
    set("playing", this.model ? this.playing : null);
    set("over", this.model ? this.model.over : null);
    set("speed", this.model ? this.speed : null);
    set("shown", this.model ? this.shown : null);
    set("sprites", this.model ? [...this.sprites.values()].filter((each) => each.loaded).length : null); // how many fighters show their picture
    set("animated", this.model ? [...this.sprites.values()].filter((each) => each.player).length : null); // how many of them play an animation
    if (!this.model) {
      set("menu", null);
      set("hero", null);
    }
    if (this.layer) this.#paint();
  }
}

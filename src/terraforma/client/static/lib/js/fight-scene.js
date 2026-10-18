// The fight scene of the stage: the fighters of both sides drawn on the stage's design resolution, and the server's rounds played back
// as they arrive over the fight socket, one event at a time (what is said, who moves, how much each gauge falls), at a pace the player
// can speed up or skip. The server decides everything; this only shows the events it stored. The screen is built from the fight's view
// (who stands, with how much) and kept up to date by the same events, so a page that opens in the middle of a fight draws it as it is.
//
// The motion uses the Web Animations API and waits for each animation to finish before the next event, so the pace is the pacing
// of the events and nothing is timed against the clock. A player who asks the system for less motion sees the same text and gauges, and
// no movement. A fighter whose sheet has the animation also plays it once for what happens (it attacks, is hit, dies, flees) and goes
// back to its "base"; a sheet without that name just keeps its "base", and a dead fighter holds the last frame of its "die".

import { drawFrame, frameRect } from "./art.js";
import { h, replace } from "./dom.js";
import { FightMenu } from "./fight-menu.js";
import { FightModel } from "./fight-model.js";
import { playerSource } from "./fight-source.js";

const SPEEDS = [1, 2, 4];
const READ_MS = 650; // how long an event stays before the next at normal speed
const MOVE_MS = 260; // how long one movement takes at normal speed
const SPRITE = 48; // the size of a fighter's picture on the card (the sprite sheet's frame is scaled to it)
// The animation a fighter plays for what happens to it (an acting fighter's own is named by the model: attack_close, skill, spell, item).
const CUES = { damage: "hit", died: "die", fled: "flee" };

const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

export class FightScene {
  /** $stage is the element that carries the data attributes; $host the element the scene's layer goes in; $source where the fight comes
   * from (fight-source.js: the logged-in player's unless a guest's is given). */
  constructor(shell, { stage, host, source = playerSource }) {
    this.shell = shell;
    this.source = source;
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
    this.at = 0; // the round being played (a replay's source says where the fight stood at the start of one)
    this.run = 0; // changes when the scene closes, so a playback in progress stops
    this.moving = new Set(); // the animations in flight
    this.sprites = new Map(); // fighter key -> { canvas, loaded, frame, player, cue, hold } for each fighter whose personality has fight art
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
      view = await this.source.view(id);
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
    this.link = this.source.link(id, {
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

  /** Asks the server how the fight stands, and shows that if nothing is being played. $force is for the playback itself, which has asked
   * while it plays (a replay has all its rounds queued at once, so a queue is no sign that the view would be stale). */
  async #resync(run, force = false) {
    if (!force && (this.playing || this.queue.length)) return;
    try {
      const view = await this.source.view(this.model.id, this.at);
      if (run !== this.run || (!force && (this.playing || this.queue.length))) return;
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
      this.at = round.round;
      for (const event of round.events) {
        if (run !== this.run) return;
        const result = this.model.apply(event);
        this.shown += 1;
        if (result.text) this.#say(...result.text);
        this.#paint();
        await this.#play(run, result.marks);
        if (result.reload) {
          this.playing = false;
          await this.#resync(run, Boolean(this.source.replay));
          this.playing = true;
        }
      }
      if (run !== this.run) return;
      this.model.endRound(round.round, round.over, round.deadline);
      this.#paint();
      // Playing the round out counts as play time (up to the game's cap since it played); a failure here is not the player's concern.
      this.source.playbackDone(this.model.id, round.round).catch(() => {});
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
    if (motion && !this.skipping) for (const mark of marks) this.#mark(mark);
    await Promise.all(marks.map((mark) => (motion ? this.#move(mark) : null)));
    if (run === this.run && !this.skipping) await this.#wait(READ_MS / this.speed);
  }

  #wait(ms) {
    return new Promise((done) => {
      this.waiting = done;
      setTimeout(done, ms);
    });
  }

  /** The animation of the fighter's picture that goes with a mark (the card's own movement is #move). */
  #mark(mark) {
    const sprite = this.sprites.get(mark.key);
    if (!sprite?.player) return;
    if (mark.kind === "revived") {
      this.#endCue(mark.key, sprite);
      this.#drawSprite(sprite);
      return;
    }
    const name = mark.cue ?? CUES[mark.kind];
    if (name) this.#cue(mark.key, name);
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
    if (this.source.replay) return; // (nobody commands a fight played back)
    try {
      const view = await this.source.view(this.model.id);
      if (run === this.run && this.model) {
        this.model.refresh(view);
        this.#data(); // (which paints)
      }
    } catch {
      // (the menu keeps what it had; the next round asks again)
    }
  }

  /** Draws the fight again from the model (the menu calls it when it changes something). */
  repaint() {
    this.#paint();
  }

  /** Says the player who commands the monsters is back after the AI took them over: they command again from the next round. */
  async comeBack() {
    const model = this.model;
    try {
      await this.source.back(model.id);
    } catch (error) {
      this.#say("fight.back_failed", { reason: error.message });
      return;
    }
    if (this.model === model) await this.refreshMenu();
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

  /** Which way a fighter faces the viewer: the viewer's own side is seen from behind ("up"), the other side from the front ("down"). A sheet
   * with no animation for a view plays the one it has (the fallbacks of animation.js). */
  #view(fighter) {
    return this.#side(fighter) === "left" ? "up" : "down";
  }

  #build() {
    this.#clearSprites();
    this.layer?.remove();
    this.cards = new Map();
    const sides = { left: h("div", { class: "fight-side left" }), right: h("div", { class: "fight-side right" }) };
    for (const [key, fighter] of this.model.fighters) {
      const card = h(
        "div",
        { class: "fighter", "data-fighter": key, "data-side": this.#side(fighter), "data-view": this.#view(fighter) },
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
    this.backButton = h("button", { type: "button", id: "fight-back", class: "primary", hidden: true, onclick: () => this.comeBack() }, this.t("fight.back"));
    this.leaveButton = h("button", { type: "button", id: "fight-leave", class: "primary", hidden: true, onclick: () => this.stage.dispatchEvent(new CustomEvent("fight-leave")) }, this.t("fight.leave"));
    this.lines = h("ol", { class: "fight-log", id: "fight-log", role: "log", "aria-label": this.t("fight.log") });
    this.layer = h(
      "div",
      { class: "fight-layer", id: "fight-layer", role: "group", "aria-label": this.t("fight.label") },
      h("div", { class: "fight-bar" }, this.statusLine, this.skipButton, this.speedButton, this.backButton, this.leaveButton),
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
    this.sprites.set(key, { canvas, loaded: null, frame: 0, player: null, cue: null, hold: false });
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
        const fighter = this.model.fighter(key);
        const art = fighter?.art;
        const loaded = await this.shell.art.load(art).catch(() => null);
        if (!loaded?.source || run !== this.run || this.sprites.get(key) !== sprite) return;
        sprite.loaded = loaded;
        sprite.frame = typeof art === "object" ? (art.frame ?? 0) : 0;
        sprite.player = loaded.animations && art.animations && !reducedMotion() ? loaded.animations.play("base", { direction: this.#view(fighter) }) : null;
        sprite.canvas.hidden = false;
        if (sprite.player && !fighter.alive && !fighter.fled) this.#cue(key, "die"); // (a fight that opens with a fighter down shows it down)
        this.#drawSprite(sprite);
      }),
    );
    if (run !== this.run) return;
    const playing = [...this.sprites.values()].filter((each) => each.player);
    this.stopSprites = playing.length
      ? this.shell.art.clock.subscribe(() => {
          for (const [key, each] of this.sprites) {
            if (!each.player) continue;
            if (each.cue && !each.hold && each.cue.done) this.#endCue(key, each);
            const player = each.cue ?? each.player;
            const at = `${player.name}:${(each.hold ? player.frameAt(Infinity) : player.frameAt()).index}`;
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
      const player = sprite.cue ?? sprite.player;
      const shows = sprite.hold ? player.frameAt(Infinity) : player.frameAt(); // (a held death shows its last frame)
      sprite.shown = `${player.name}:${shows.index}`;
      drawFrame(context, loaded, shows.frame, 0, 0, SPRITE, SPRITE, shows.ops);
    } else if (loaded.frame) {
      const rect = frameRect(loaded, sprite.frame);
      context.drawImage(loaded.source, rect.x, rect.y, rect.w, rect.h, 0, 0, SPRITE, SPRITE);
    } else {
      context.drawImage(loaded.source, 0, 0, SPRITE, SPRITE);
    }
  }

  /** Plays $name once on a fighter's picture, if its sheet has that animation for the view (a sheet without it keeps its "base"). "die" is
   * held. The card says what it plays in data-cue, which goes when the animation ends. */
  #cue(key, name) {
    const sprite = this.sprites.get(key);
    const fighter = this.model?.fighter(key);
    if (!sprite?.player || !fighter) return;
    const direction = this.#view(fighter);
    const animations = sprite.loaded.animations;
    if (!animations.find(name, direction)?.startsWith(name)) return;
    sprite.cue = animations.play(name, { direction, once: true });
    sprite.hold = name === "die";
    sprite.shown = null;
    const card = this.cards.get(key);
    if (card) card.dataset.cue = name;
    this.#drawSprite(sprite);
  }

  #endCue(key, sprite) {
    sprite.cue = null;
    sprite.hold = false;
    sprite.shown = null;
    delete this.cards.get(key)?.dataset.cue;
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
    this.backButton.hidden = !(this.model.seat?.absent && !this.model.over); // (the AI took the monsters over after a missed round)
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
    set("absent", this.model?.seat ? this.model.seat.absent : null); // set only for the player who commands monsters
    set("sprites", this.model ? [...this.sprites.values()].filter((each) => each.loaded).length : null); // how many fighters show their picture
    set("animated", this.model ? [...this.sprites.values()].filter((each) => each.player).length : null); // how many of them play an animation
    if (!this.model) {
      set("menu", null);
      set("hero", null);
    }
    if (this.layer) this.#paint();
  }
}

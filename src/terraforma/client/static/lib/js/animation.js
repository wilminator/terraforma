// Animations: the named animations of one tilesheet, read from NAME.anim.json (the format is described in the README, Art; the server
// checks the files when it starts, terraforma/animations.py, and this file reads them again so a page never trusts them).
//
//   { "animations": { "base": { "frames": [0, { "frame": 1, "ms": 250, "flip_x": true }], "ms": 100, "loop": true, "sync": "shared" } },
//     "aliases":    { "stand_left": { "of": "stand_right", "flip_x": true } } }
//
// parseAnimations(data, frameCount) gives an AnimationSet. $set.play(name, { direction }) gives a Player, or null when the set has nothing
// to play for that name; a player says which frame of the sheet to draw and how to transform it ($player.frameAt(), drawn with
// art.js drawFrame). One Clock drives every player: it reads the time once for all of them and, for the scenes that redraw on it, runs one
// requestAnimationFrame loop. A player whose clock is "still" (the player asked for less motion) shows the first frame of its animation.
//
// Which animation plays (candidates): the name asked for, then "stand", then "base", each as NAME_DIRECTION (when a direction up, down,
// left or right is given), plain NAME, and NAME_down (a missing direction becomes down, so does no direction at all). So a fight sheet's missing "hit" plays "base",
// and a map sheet's missing "move_left" plays "stand_left", or "stand_down" if that is missing too.
//
// A transform (flip_x, flip_y, scale, rotate in degrees clockwise, pivot [x, y] in pixels of the frame, the frame's center if left out) is
// applied flip first, then scale, then rotate. A frame's own transform comes first, then each alias it was reached through, the one
// nearest the animation first, so a "flip_x" alias of a "flip_x" alias is the picture as it was drawn.

export const DIRECTIONS = ["up", "down", "left", "right"];
const NAME = /^[a-z][a-z0-9_]{0,63}$/;

/** The names tried, in order, for $name and $direction (null: no direction). */
export function candidates(name, direction = null) {
  const names = [];
  const add = (each) => names.includes(each) || names.push(each);
  for (const action of name === "stand" ? ["stand"] : [name, "stand"]) {
    if (direction) add(`${action}_${direction}`);
    add(action);
    add(`${action}_down`);
  }
  add("base");
  return names;
}

const isIdentity = (op) => !op.flip_x && !op.flip_y && op.scale === 1 && op.rotate === 0;

function transformOf(source) {
  const op = {
    flip_x: source.flip_x === true,
    flip_y: source.flip_y === true,
    scale: source.scale ?? 1,
    rotate: source.rotate ?? 0,
    pivot: source.pivot ?? null,
  };
  if (!(op.scale > 0 && op.scale <= 16)) throw new Error(`scale ${op.scale} must be above 0 and at most 16`);
  if (!(op.rotate >= -360 && op.rotate <= 360)) throw new Error(`rotate ${op.rotate} must be between -360 and 360 degrees`);
  if (op.pivot !== null && !(Array.isArray(op.pivot) && op.pivot.length === 2 && op.pivot.every(Number.isFinite))) throw new Error("pivot is [x, y]");
  return op;
}

/** The AnimationSet for $data (a parsed NAME.anim.json); $frameCount, when the sheet's is known, limits every frame. Throws Error saying what is wrong. */
export function parseAnimations(data, frameCount = null, clock = sharedClock) {
  if (!data || typeof data.animations !== "object" || data.animations === null) throw new Error("an animation file has animations");
  const animations = new Map();
  for (const [name, each] of Object.entries(data.animations)) {
    if (!NAME.test(name)) throw new Error(`animation name "${name}" is 1-64 lowercase letters, digits or _, starting with a letter`);
    if (!Array.isArray(each.frames) || each.frames.length === 0) throw new Error(`animation "${name}" needs frames`);
    const ms = each.ms ?? 100;
    if (!(ms >= 1)) throw new Error(`animation "${name}": ms must be at least 1`);
    const frames = each.frames.map((frame) => {
      const entry = typeof frame === "number" ? { frame } : frame;
      if (!Number.isInteger(entry.frame) || entry.frame < 0) throw new Error(`animation "${name}": ${JSON.stringify(frame)} is not a frame`);
      if (frameCount !== null && entry.frame >= frameCount) throw new Error(`animation "${name}" shows frame ${entry.frame}, but the sheet has ${frameCount} frames`);
      const time = entry.ms ?? ms;
      if (!(time >= 1)) throw new Error(`animation "${name}": ms must be at least 1`);
      return { frame: entry.frame, ms: time, op: transformOf(entry) };
    });
    const sync = each.sync ?? "shared";
    if (sync !== "shared" && sync !== "random") throw new Error(`animation "${name}": sync is shared or random`);
    animations.set(name, { frames, loop: each.loop !== false, sync, total: frames.reduce((sum, frame) => sum + frame.ms, 0) });
  }
  if (animations.size === 0) throw new Error("a file needs at least one animation");
  const aliases = new Map();
  for (const [name, each] of Object.entries(data.aliases ?? {})) {
    if (!NAME.test(name)) throw new Error(`alias name "${name}" is 1-64 lowercase letters, digits or _, starting with a letter`);
    if (animations.has(name)) throw new Error(`"${name}" is both an animation and an alias`);
    aliases.set(name, { of: each.of, op: transformOf(each) });
  }
  for (const name of aliases.keys()) {
    const seen = new Set([name]);
    let target = aliases.get(name).of;
    while (aliases.has(target)) {
      if (seen.has(target)) throw new Error(`alias loop: "${name}" leads back to itself`);
      seen.add(target);
      target = aliases.get(target).of;
    }
    if (!animations.has(target)) throw new Error(`alias "${name}" is of "${target}", which is not an animation or an alias`);
  }
  return new AnimationSet(animations, aliases, clock);
}

export class AnimationSet {
  #animations;
  #aliases;
  #clock;
  #clips = new Map();

  constructor(animations, aliases, clock) {
    this.#animations = animations;
    this.#aliases = aliases;
    this.#clock = clock;
  }

  /** Every name a player can ask for: the animations, then the aliases. */
  names() {
    return [...this.#animations.keys(), ...this.#aliases.keys()];
  }

  has(name) {
    return this.#animations.has(name) || this.#aliases.has(name);
  }

  /** The animation called exactly $name, its aliases worked out: { frames: [{ frame, ms, ops }], total, loop, sync }; null if there is none. */
  clip(name) {
    if (!this.has(name)) return null;
    if (!this.#clips.has(name)) {
      const chain = [];
      let target = name;
      while (this.#aliases.has(target)) {
        chain.push(this.#aliases.get(target).op);
        target = this.#aliases.get(target).of;
      }
      const animation = this.#animations.get(target);
      const outer = chain.reverse().filter((op) => !isIdentity(op));
      const frames = animation.frames.map((each) => ({ frame: each.frame, ms: each.ms, ops: [each.op, ...outer].filter((op) => !isIdentity(op)) }));
      this.#clips.set(name, { frames, total: animation.total, loop: animation.loop, sync: animation.sync });
    }
    return this.#clips.get(name);
  }

  /** The name that plays for $name and $direction (see candidates), or null if the set has none of them. */
  find(name, direction = null) {
    return candidates(name, direction).find((each) => this.has(each)) ?? null;
  }

  /** A Player of the animation for $name (and optional $direction), or null. $clock replaces the shared clock (for tests). */
  play(name, { direction = null, clock = this.#clock } = {}) {
    const found = this.find(name, direction);
    return found === null ? null : new Player(this.clip(found), clock, found);
  }
}

export class Player {
  #clip;
  #clock;
  #started;
  #offset;

  constructor(clip, clock, name) {
    this.#clip = clip;
    this.#clock = clock;
    this.#started = clock.now();
    this.#offset = clip.sync === "random" && clip.loop ? clock.random() * clip.total : 0;
    this.name = name;
  }

  get done() {
    return !this.#clip.loop && !this.#clock.still && this.#clock.now() - this.#started >= this.#clip.total;
  }

  /** What to draw at $now (the clock's time): { frame (of the sheet), ops (transforms, see matrixOf), index (in the animation) }. */
  frameAt(now = this.#clock.now()) {
    const { frames, total, loop } = this.#clip;
    let index = 0;
    if (!this.#clock.still) {
      // (a shared loop is in step with every other: it counts from the clock's start; the rest count from when they began)
      let elapsed = loop && this.#clip.sync === "shared" ? now : now - this.#started + this.#offset;
      elapsed = loop ? ((elapsed % total) + total) % total : Math.min(Math.max(elapsed, 0), total - 1e-6);
      while (index < frames.length - 1 && elapsed >= frames[index].ms) elapsed -= frames[index++].ms;
    }
    return { frame: frames[index].frame, ops: frames[index].ops, index };
  }
}

// --- drawing math -------------------------------------------------------------------------------------

/** Canvas matrix [a, b, c, d, e, f] of "m, after n" (n is applied to a point first). */
function multiply(m, n) {
  return [
    m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
    m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
    m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
  ];
}

/** The matrix of the transforms $ops (applied in order) on a picture $width by $height pixels; each is about its pivot or the picture's center. */
export function matrixOf(ops, width, height) {
  let matrix = [1, 0, 0, 1, 0, 0];
  for (const op of ops) {
    const [px, py] = op.pivot ?? [width / 2, height / 2];
    const angle = (op.rotate * Math.PI) / 180;
    const [cos, sin] = [Math.cos(angle), Math.sin(angle)];
    const sx = op.scale * (op.flip_x ? -1 : 1);
    const sy = op.scale * (op.flip_y ? -1 : 1);
    // (translate to the pivot) after (rotate) after (flip and scale) after (translate the pivot to the origin)
    const linear = [cos * sx, sin * sx, -sin * sy, cos * sy, 0, 0];
    linear[4] = px - (linear[0] * px + linear[2] * py);
    linear[5] = py - (linear[1] * px + linear[3] * py);
    matrix = multiply(linear, matrix);
  }
  return matrix;
}

// --- the one timer ------------------------------------------------------------------------------------

const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/**
 * The time every animation shares. now() is milliseconds on one clock; subscribe(listener) calls listener(now) on every animation frame
 * (one requestAnimationFrame loop for all listeners, running only while there are some) and returns the function that stops it. $options
 * replace the browser's, for tests: now(), request(callback), cancel(id), still() and random().
 */
export class Clock {
  #now;
  #request;
  #cancel;
  #still;
  #listeners = new Set();
  #pending = null;

  constructor({ now, request, cancel, still, random } = {}) {
    this.#now = now ?? (() => performance.now());
    this.#request = request ?? ((callback) => requestAnimationFrame(callback));
    this.#cancel = cancel ?? ((id) => cancelAnimationFrame(id));
    this.#still = still ?? reducedMotion;
    this.random = random ?? Math.random;
  }

  now() {
    return this.#now();
  }

  /** True when the player has asked for less motion: nothing moves. */
  get still() {
    return this.#still();
  }

  subscribe(listener) {
    if (this.still) return () => {}; // (nothing changes: no loop)
    this.#listeners.add(listener);
    if (this.#pending === null) this.#pending = this.#request(() => this.#tick());
    return () => {
      this.#listeners.delete(listener);
      if (this.#listeners.size === 0 && this.#pending !== null) {
        this.#cancel(this.#pending);
        this.#pending = null;
      }
    };
  }

  #tick() {
    this.#pending = null;
    const now = this.now();
    for (const listener of [...this.#listeners]) listener(now);
    if (this.#listeners.size > 0) this.#pending = this.#request(() => this.#tick());
  }
}

/** The clock the engine's scenes use. */
export const sharedClock = new Clock();

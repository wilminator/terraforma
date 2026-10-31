// Sound for the game client: short effects for what happens, and one looping tune for the scene on the stage. The engine carries no
// sounds of its own: a game's module says which file goes with which cue (the files are among the game's assets), and a game with none
// stays silent and shows no sound button.
//
//   shell.audio.add({ "panel.open": "audio/sfx/panel_open.mp3", "fight.damage": "audio/sfx/hit.mp3" });
//   shell.audio.addMusic({ map: "audio/music/map.mp3", fight: "audio/music/combat.mp3" });
//
// The engine plays a cue when the thing happens, and sets the tune when the scene changes:
//   cues    panel.open, panel.close, menu.confirm, menu.cancel, fight.start, fight.victory, fight.defeat, fight.over (a fight you only watched),
//           and for each event of a fight's log: fight.act (a turn's action; fight.<name> is tried first, e.g. fight.attack_close), fight.damage,
//           fight.miss, fight.heal, fight.died, fight.fled, fight.revived, fight.defend, fight.status, fight.level
//   tunes   map, fight
// A game's own code may call shell.audio.play("name") and shell.audio.music("name") (music(null) stops it) with names of its own.
//
// A browser keeps a page silent until the player has touched it, so a tune asked for earlier waits for the first tap or key. The player's
// choice (sound off, and the two volumes) is kept in the browser's storage and read back by the next visit.

const KEY = "terraforma.sound";

/** The cues of one mark of a fight (see fight-model.js), most specific first. */
export function cuesOf(mark) {
  switch (mark.kind) {
    case "act":
      return mark.cue ? [`fight.${mark.cue}`, "fight.act"] : ["fight.act"];
    case "damage":
    case "miss":
    case "heal":
    case "died":
    case "fled":
    case "revived":
    case "defend":
    case "status":
    case "level":
      return [`fight.${mark.kind}`];
    default:
      return []; // (a turn starting, a stat changing: nothing to hear)
  }
}

function browserStorage() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null; // (storage can be refused outright: a private window, blocked site data)
  }
}

const clamp = (value) => Math.min(1, Math.max(0, Number.isFinite(value) ? value : 1));

export class Sounds {
  #files = new Map();
  #tunes = new Map();
  #url;
  #make;
  #storage;
  #target;
  #changed = new Set();
  #wanted = null; // the tune the stage asked for (it may not be playing yet: the page may not have been touched)
  #playing = null; // { name, element }
  #waiting = false;
  muted = false;
  effects = 1;
  music_volume = 1;

  /** $url turns an asset file name into an address; $make and $storage are the browser's own unless a test hands in others; $target hears the first touch. */
  constructor({ url, make = (src) => new globalThis.Audio(src), storage = browserStorage(), target = globalThis.document } = {}) {
    this.#url = url;
    this.#make = make;
    this.#storage = storage;
    this.#target = target;
    this.#restore();
  }

  /** Whether a game has given the page any sound at all (the sound button shows only then). */
  get available() {
    return this.#files.size > 0 || this.#tunes.size > 0;
  }

  /** Says which file goes with each effect cue: { cue: "audio/sfx/hit.mp3" }. A cue named again takes the new file. */
  add(cues) {
    for (const [name, file] of Object.entries(cues)) this.#files.set(name, file);
    this.#changed.forEach((listener) => listener());
  }

  /** Says which file goes with each tune: { map: "audio/music/map.mp3" }. */
  addMusic(tunes) {
    for (const [name, file] of Object.entries(tunes)) this.#tunes.set(name, file);
    this.#changed.forEach((listener) => listener());
    if (this.#wanted && !this.#playing) this.#start();
  }

  has(name) {
    return this.#files.has(name);
  }

  /** Plays the first of $names that the game gave a file; returns the name that played, or null (silent, muted, or nothing for these names). */
  play(...names) {
    const name = names.find((each) => this.#files.has(each));
    if (name === undefined || this.muted || this.effects === 0) return null;
    const element = this.#make(this.#url(this.#files.get(name)));
    element.volume = this.effects;
    this.#attempt(element);
    return name;
  }

  /** Sets the tune of the scene (null for none). The same tune again keeps playing where it is. */
  music(name) {
    this.#wanted = name ?? null;
    if (this.#playing && this.#playing.name !== this.#wanted) this.#stop();
    if (this.#wanted && !this.#playing) this.#start();
  }

  /** Changes what the player chose: { muted, effects, music } (any of them), keeps it in the browser, and tells whoever listens. */
  set(choice) {
    if ("muted" in choice) this.muted = Boolean(choice.muted);
    if ("effects" in choice) this.effects = clamp(choice.effects);
    if ("music" in choice) this.music_volume = clamp(choice.music);
    try {
      this.#storage?.setItem(KEY, JSON.stringify({ muted: this.muted, effects: this.effects, music: this.music_volume }));
    } catch {
      // (the choice holds for this visit)
    }
    if (this.#playing) {
      if (this.muted || this.music_volume === 0) this.#stop();
      else this.#playing.element.volume = this.music_volume;
    }
    if (this.#wanted && !this.#playing) this.#start();
    this.#changed.forEach((listener) => listener());
  }

  /** Calls $listener whenever the files or the player's choice change; returns a function that stops it. */
  onChange(listener) {
    this.#changed.add(listener);
    return () => this.#changed.delete(listener);
  }

  #restore() {
    try {
      const saved = JSON.parse(this.#storage?.getItem(KEY) ?? "null");
      if (saved && typeof saved === "object") {
        this.muted = saved.muted === true;
        this.effects = clamp(saved.effects ?? 1);
        this.music_volume = clamp(saved.music ?? 1);
      }
    } catch {
      // (nothing kept, or not readable: the defaults)
    }
  }

  #start() {
    const file = this.#tunes.get(this.#wanted);
    if (file === undefined || this.muted || this.music_volume === 0) return;
    const element = this.#make(this.#url(file));
    element.loop = true;
    element.volume = this.music_volume;
    this.#playing = { name: this.#wanted, element };
    this.#attempt(element, (error) => {
      if (this.#playing?.element === element) this.#playing = null;
      // (a browser keeps the page silent until it is touched: try again then. A file it cannot play is left alone until the tune changes)
      if (error?.name === "NotAllowedError") this.#waitForTouch();
    });
  }

  #stop() {
    const { element } = this.#playing;
    this.#playing = null;
    element.pause?.();
  }

  /** Starts $element; a browser that refuses (no touch yet, a file it cannot play) is not an error for the page. */
  #attempt(element, refused = () => {}) {
    try {
      element.play()?.catch?.(refused);
    } catch (error) {
      refused(error);
    }
  }

  #waitForTouch() {
    if (this.#waiting || !this.#target) return;
    this.#waiting = true;
    const touched = () => {
      this.#waiting = false;
      for (const kind of ["pointerdown", "keydown"]) this.#target.removeEventListener(kind, touched, true);
      if (this.#wanted && !this.#playing) this.#start();
    };
    for (const kind of ["pointerdown", "keydown"]) this.#target.addEventListener(kind, touched, true);
  }
}

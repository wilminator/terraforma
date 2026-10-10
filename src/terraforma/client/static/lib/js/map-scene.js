// The map scene of the stage: the map the party stands on, drawn at the stage's design resolution (tiles of TILE pixels, the view
// centered on the party), and the party's walk over it. The server decides everything: a tap proposes a route (the server fills in the
// gaps or says why not), the party walks it one tile at a time and the server confirms each tile; if it does not, the party is put
// back where it really is. A walk stops at a fight, a town or the event at an edge of the map; a chest, door or sign runs its script
// in the Talk panel. (The fight itself is the next scene, drawn on this same stage.)

import * as api from "./api.js";
import { frameRect } from "./art.js";
import { DESIGN_HEIGHT, DESIGN_WIDTH } from "./stage.js";

export const TILE = 32;
const STEP_MS = 140; // how long the party takes over one tile
const KEYS = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0], w: [0, -1], s: [0, 1], a: [-1, 0], d: [1, 0] };
const MARK = { sign: "S", door: "D", chest: "C" };

/** Which tile the point ($x, $y) of the view falls on, given the view's top-left corner. */
export function tileAt(corner, x, y) {
  return { x: Math.floor((corner.x + x) / TILE), y: Math.floor((corner.y + y) / TILE) };
}

/** The top-left corner of the view: centered on ($x, $y) in pixels, held inside the map unless it wraps (then it just follows). */
export function viewCorner(map, x, y) {
  const corner = (center, size, wrap, view) => (wrap ? center - view / 2 : size <= view ? (size - view) / 2 : Math.min(Math.max(center - view / 2, 0), size - view));
  return {
    x: corner(x, map.width * TILE, map.wrap_x, DESIGN_WIDTH),
    y: corner(y, map.height * TILE, map.wrap_y, DESIGN_HEIGHT),
  };
}

export class MapScene {
  constructor(shell, roster, { stage, canvas, note }) {
    this.shell = shell;
    this.roster = roster;
    this.stage = stage;
    this.canvas = canvas;
    this.noteElement = note;
    this.context = canvas.getContext("2d");
    this.map = null;
    this.art = [];
    this.partyId = null;
    this.tile = { x: 0, y: 0 }; // where the server says the party is
    this.at = { x: 0, y: 0 }; // where it is drawn (between tiles while it walks)
    this.walking = false;
    this.t = (key, values) => shell.text.get(key, values);
    canvas.addEventListener("pointerdown", (event) => this.#tapped(event));
    stage.addEventListener("keydown", (event) => this.#key(event));
    roster.onChange(() => this.#follow());
    shell.conversation.onChange(() => this.#conversationMoved());
    this.#follow();
  }

  // --- which party the stage shows -----------------------------------------------------------------

  /** The party of the team the player is looking at (the Party panel's), or null. */
  #wanted() {
    return this.roster.team(this.roster.teamId)?.party ?? null;
  }

  async #follow() {
    const wanted = this.#wanted();
    if (wanted === this.partyId) return;
    this.partyId = wanted;
    this.walking = false;
    if (wanted === null) {
      this.map = null;
      this.#data();
      this.#draw();
      return;
    }
    await this.refresh();
  }

  /** Asks the server where the party is (a warp or a reload moves it) and draws that. */
  async refresh() {
    if (this.partyId === null) return;
    const id = this.partyId;
    try {
      const where = await api.get(`/api/parties/${id}/route`);
      if (id !== this.partyId) return;
      await this.#load(where.map, where.revision);
      this.tile = { x: where.x, y: where.y };
      if (!this.walking) this.at = { x: where.x * TILE, y: where.y * TILE };
    } catch (error) {
      this.#say(error.message);
    }
    this.#data();
    this.#draw();
  }

  async #load(name, revision) {
    if (this.map?.map === name && this.map.revision === revision) return;
    this.map = await api.get(`/api/maps/${name}`);
    // (a sheet reference names the frame its tile kind draws; a plain file is the one picture)
    this.art = await Promise.all(
      this.map.tileset.map(async (kind) => {
        const loaded = kind.art ? await this.shell.art.load(kind.art).catch(() => null) : null;
        return loaded && { loaded, frame: typeof kind.art === "object" ? (kind.art.frame ?? 0) : 0 };
      }),
    );
  }

  #conversationMoved() {
    // (a script may have warped the party to another map, or moved it: ask where it is)
    this.refresh();
  }

  // --- drawing -------------------------------------------------------------------------------------

  #data() {
    const set = (name, value) => (value === null ? delete this.stage.dataset[name] : (this.stage.dataset[name] = String(value)));
    set("map", this.map?.map ?? null);
    set("partyX", this.map ? this.tile.x : null);
    set("partyY", this.map ? this.tile.y : null);
    set("walking", this.map ? this.walking : null);
    set("art", this.map ? this.art.filter(Boolean).length : null); // how many of the map's tile kinds have their picture
    this.stage.setAttribute("aria-label", this.map ? this.t("map.label", { map: this.map.title || this.map.map, x: this.tile.x, y: this.tile.y }) : this.t("play.stage"));
  }

  #say(text) {
    this.noteElement.textContent = text;
  }

  #draw() {
    const { context: c, canvas } = this;
    c.imageSmoothingEnabled = false;
    c.fillStyle = "#101612";
    c.fillRect(0, 0, canvas.width, canvas.height);
    if (!this.map) {
      c.fillStyle = "#cfd8cf";
      c.font = "14px system-ui, sans-serif";
      c.textAlign = "center";
      c.fillText(this.t("map.nothing"), canvas.width / 2, canvas.height / 2);
      return;
    }
    const map = this.map;
    const corner = viewCorner(map, this.at.x + TILE / 2, this.at.y + TILE / 2);
    const first = tileAt(corner, 0, 0);
    const last = tileAt(corner, DESIGN_WIDTH - 1, DESIGN_HEIGHT - 1);
    for (let ty = first.y; ty <= last.y; ty++) {
      for (let tx = first.x; tx <= last.x; tx++) {
        const kind = this.#kindAt(tx, ty);
        if (kind === null) continue;
        this.#drawTile(kind, tx * TILE - corner.x, ty * TILE - corner.y);
      }
    }
    for (const thing of map.objects) {
      const spot = this.#screenOf(thing, corner);
      if (!spot) continue;
      c.fillStyle = "#e8d27a";
      c.fillRect(spot.x + 6, spot.y + 6, TILE - 12, TILE - 12);
      c.fillStyle = "#2a2417";
      c.font = "bold 14px system-ui, sans-serif";
      c.textAlign = "center";
      c.fillText(MARK[thing.kind] ?? "?", spot.x + TILE / 2, spot.y + TILE / 2 + 5);
    }
    const px = this.at.x - corner.x + TILE / 2;
    const py = this.at.y - corner.y + TILE / 2;
    c.fillStyle = "#d94a3d";
    c.strokeStyle = "#ffffff";
    c.lineWidth = 2;
    c.beginPath();
    c.arc(px, py, TILE / 2 - 5, 0, Math.PI * 2);
    c.fill();
    c.stroke();
  }

  /** The tileset index at ($tx, $ty), wrapping where the map does; null off the map. */
  #kindAt(tx, ty) {
    const { width, height, wrap_x: wrapX, wrap_y: wrapY, tiles } = this.map;
    const x = wrapX ? ((tx % width) + width) % width : tx;
    const y = wrapY ? ((ty % height) + height) % height : ty;
    return x < 0 || y < 0 || x >= width || y >= height ? null : tiles[y][x];
  }

  #drawTile(kind, x, y) {
    const art = this.art[kind];
    const c = this.context;
    if (art?.loaded.source) {
      const { loaded, frame } = art;
      if (loaded.frame) {
        const rect = frameRect(loaded, frame);
        c.drawImage(loaded.source, rect.x, rect.y, rect.w, rect.h, x, y, TILE, TILE);
      } else {
        c.drawImage(loaded.source, x, y, TILE, TILE);
      }
      return;
    }
    c.fillStyle = this.map.tileset[kind]?.passable === false ? "#2b2f2c" : "#3d5a3a";
    c.fillRect(x, y, TILE, TILE);
  }

  /** Where an object is on the screen (its tile, wherever the view wraps to), or null if it is out of view. */
  #screenOf(thing, corner) {
    const { width, height, wrap_x: wrapX, wrap_y: wrapY } = this.map;
    const near = (tile, size, wrap, from) => {
      if (!wrap) return tile * TILE - from;
      let spot = tile * TILE - from;
      const span = size * TILE;
      spot = ((spot % span) + span) % span;
      return spot > span - TILE ? spot - span : spot;
    };
    const x = near(thing.x, width, wrapX, corner.x);
    const y = near(thing.y, height, wrapY, corner.y);
    return x > -TILE && y > -TILE && x < DESIGN_WIDTH && y < DESIGN_HEIGHT ? { x, y } : null;
  }

  // --- input ---------------------------------------------------------------------------------------

  #tapped(event) {
    if (!this.map) return;
    const rect = this.canvas.getBoundingClientRect();
    const corner = viewCorner(this.map, this.at.x + TILE / 2, this.at.y + TILE / 2);
    const spot = tileAt(corner, ((event.clientX - rect.left) * DESIGN_WIDTH) / rect.width, ((event.clientY - rect.top) * DESIGN_HEIGHT) / rect.height);
    const tile = this.#wrapped(spot);
    const thing = this.map.objects.find((each) => each.x === tile.x && each.y === tile.y);
    this.stage.focus();
    if (thing) this.useObject(thing);
    else this.goTo(spot);
  }

  #wrapped(tile) {
    const { width, height, wrap_x: wrapX, wrap_y: wrapY } = this.map;
    return { x: wrapX ? ((tile.x % width) + width) % width : tile.x, y: wrapY ? ((tile.y % height) + height) % height : tile.y };
  }

  #key(event) {
    const move = KEYS[event.key];
    if (!move || !this.map || event.ctrlKey || event.metaKey || event.altKey) return;
    event.preventDefault();
    this.goTo({ x: this.tile.x + move[0], y: this.tile.y + move[1] });
  }

  // --- walking -------------------------------------------------------------------------------------

  /** Proposes a route to $tile and walks it; whatever the server refuses is said in the note. */
  async goTo(tile) {
    if (this.walking || this.partyId === null) return;
    this.#say("");
    const id = this.partyId;
    this.walking = true;
    this.#data();
    try {
      const view = await api.post(`/api/parties/${id}/route`, { destination: this.#wrapped(tile) });
      for (let at = view.at + 1; at < view.route.length; at++) {
        const [x, y] = view.route[at];
        await this.#slide(x * TILE, y * TILE);
        const answer = await api.post(`/api/parties/${id}/route/step`, { x, y });
        if (id !== this.partyId) return;
        if (!answer.confirmed) {
          this.#say(answer.reason);
          break;
        }
        if (answer.edge) {
          await this.#event(answer.dialog);
          break;
        }
        this.tile = { x: answer.x, y: answer.y };
        if (answer.fight) {
          this.#say(this.t("map.fight"));
          break;
        }
        if (answer.town) {
          this.#say(this.t("map.town"));
          this.shell.panels.open("party");
          break;
        }
        if (answer.done) break;
      }
    } catch (error) {
      this.#say(error.message);
    } finally {
      this.walking = false;
      await this.refresh(); // (put the party where the server says it is)
    }
  }

  /** Moves the drawn party to ($x, $y) pixels over one step. */
  #slide(x, y) {
    const from = { ...this.at };
    return new Promise((done) => {
      const start = performance.now();
      const frame = (now) => {
        const progress = Math.min(1, (now - start) / STEP_MS);
        this.at = { x: from.x + (x - from.x) * progress, y: from.y + (y - from.y) * progress };
        this.#draw();
        if (progress < 1) requestAnimationFrame(frame);
        else done();
      };
      requestAnimationFrame(frame);
    });
  }

  // --- what happens in the Talk panel --------------------------------------------------------------

  /** The hero whose events run for the party: the one in a conversation now (the server picks the leader's first hero). */
  async #talkingHero() {
    for (const member of this.roster.team(this.roster.teamId)?.members ?? []) {
      const talk = await api.get(`/api/heroes/${member.hero_id}/dialog`);
      if (talk.talking) return member.hero_id;
    }
    return null;
  }

  async #event(frame) {
    const hero = await this.#talkingHero();
    if (hero !== null) this.#showFrame(hero, frame);
  }

  #showFrame(heroId, frame) {
    this.shell.conversation.show(heroId, frame);
    if (frame.window !== false) this.shell.panels.open("dialog");
    else this.#say((frame.events ?? []).filter((event) => event.type === "text").map((event) => event.text).join(" "));
  }

  /** The party's first hero uses a chest, door or sign (it must be in reach: the server says if not). */
  async useObject(thing) {
    const hero = this.roster.team(this.roster.teamId)?.members[0]?.hero_id;
    if (hero === undefined) return;
    this.#say("");
    try {
      this.#showFrame(hero, await api.post(`/api/heroes/${hero}/objects/${thing.id}/use`));
    } catch (error) {
      this.#say(error.message);
    }
  }
}

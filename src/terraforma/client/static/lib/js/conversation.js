// The conversation a hero is in with an NPC: what has been said so far and what the NPC asks next. The server runs the dialog and
// decides everything (where an answer leads is never sent here); this keeps the last answer and the lines shown, and tells whoever
// draws it when something changes. Every call answers with a frame: { npc|object, events, prompt, ended } (GET /dialog adds `talking`).

import * as api from "./api.js";

export class Conversation {
  heroId = null;
  talking = false;
  speaker = ""; // who the hero is talking to
  lines = []; // what has been said, in order
  prompt = null; // what the NPC asks now: { type: "ack" | "choice" | "activity", ... }, or null once it is over
  ended = false;
  #listeners = new Set();

  onChange(listener) {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #changed() {
    for (const listener of this.#listeners) listener();
  }

  #take(frame) {
    this.talking = frame.talking !== false;
    if (!this.talking) {
      this.prompt = null;
      this.ended = false;
      return;
    }
    this.speaker = (frame.npc ?? frame.object)?.name ?? "";
    for (const event of frame.events ?? []) if (event.type === "text") this.lines.push(event.text);
    this.prompt = frame.prompt ?? null;
    this.ended = Boolean(frame.ended);
  }

  /** Finds out whether the hero is in the middle of a conversation (a reload keeps it on the server). */
  async resume(heroId) {
    if (this.talking && this.heroId === heroId) return;
    this.heroId = heroId;
    this.lines = [];
    this.#take(await api.get(`/api/heroes/${heroId}/dialog`));
    this.#changed();
  }

  /** The hero starts talking to the NPC. */
  async talk(heroId, npcId) {
    this.heroId = heroId;
    this.lines = [];
    this.#take(await api.post(`/api/heroes/${heroId}/npcs/${npcId}/talk`));
    this.#changed();
  }

  /** Goes on: Next, or the answer picked ($choice, an option index); no $choice also cancels a question. */
  async next(choice = null) {
    this.#take(await api.post(`/api/heroes/${this.heroId}/dialog/next`, choice === null ? {} : { choice }));
    this.#changed();
  }

  /** The hero walks away. */
  async leave() {
    if (this.heroId !== null) await api.post(`/api/heroes/${this.heroId}/dialog/leave`);
    this.close();
  }

  /** Puts the finished conversation away. */
  close() {
    this.talking = false;
    this.prompt = null;
    this.ended = false;
    this.lines = [];
    this.#changed();
  }
}

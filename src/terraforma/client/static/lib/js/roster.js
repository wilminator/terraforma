// What the player has: heroes, teams and the jobs a new hero can take, read from the server and kept in step with it.
// The panels draw from here and call its methods; every call that changes something reloads, so the server stays the one truth.

import * as api from "./api.js";

export class Roster {
  heroes = [];
  teams = [];
  jobs = [];
  heroId = null; // the hero the player is acting with (the nearby list is theirs)
  teamId = null; // the team the player is looking at in the party panel
  #listeners = new Set();

  /** Calls $listener whenever the roster changes; returns a function that stops it. */
  onChange(listener) {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #changed() {
    for (const listener of this.#listeners) listener();
  }

  hero(id) {
    return this.heroes.find((each) => each.id === id) ?? null;
  }

  team(id) {
    return this.teams.find((each) => each.id === id) ?? null;
  }

  /** The team a hero is on, or null. */
  teamOf(heroId) {
    return this.teams.find((team) => team.members.some((member) => member.hero_id === heroId)) ?? null;
  }

  async load() {
    [this.heroes, this.teams, this.jobs] = await Promise.all([api.get("/api/heroes"), api.get("/api/teams"), api.get("/api/jobs")]);
    if (this.hero(this.heroId) === null) this.heroId = this.heroes[0]?.id ?? null;
    if (this.team(this.teamId) === null) this.teamId = this.teams[0]?.id ?? null;
    this.#changed();
  }

  selectHero(id) {
    this.heroId = id;
    this.#changed();
  }

  selectTeam(id) {
    this.teamId = id;
    this.#changed();
  }

  async createHero(name, job) {
    const hero = await api.post("/api/heroes", { name, job });
    this.heroId = hero.id;
    await this.load();
  }

  async renameHero(id, name) {
    await api.post(`/api/heroes/${id}/rename`, { name });
    await this.load();
  }

  async deleteHero(id) {
    await api.post(`/api/heroes/${id}/delete`);
    await this.load();
  }

  async createTeam(name) {
    const team = await api.post("/api/teams", { name });
    this.teamId = team.id;
    await this.load();
  }

  async renameTeam(id, name) {
    await api.post(`/api/teams/${id}/rename`, { name });
    await this.load();
  }

  async deleteTeam(id) {
    await api.post(`/api/teams/${id}/delete`);
    await this.load();
  }

  async addToTeam(teamId, heroId) {
    await api.post(`/api/teams/${teamId}/add-hero`, { hero_id: heroId });
    await this.load();
  }

  async removeFromTeam(teamId, heroId) {
    await api.post(`/api/teams/${teamId}/remove-hero`, { hero_id: heroId });
    await this.load();
  }
}

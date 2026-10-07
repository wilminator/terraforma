// What the player has: teams with their heroes, the jobs a new hero can take and what the game says about teams, read from
// the server and kept in step with it. The panels draw from here and call its methods; every call that changes something
// reloads, so the server stays the one truth. A hero is always on a team: heroes are made with a team or added to one.

import * as api from "./api.js";

export class Roster {
  heroes = [];
  teams = [];
  jobs = [];
  rules = { team_min: 1, team_max: 4, max_teams: 3 }; // what the game says about teams (the server's answer replaces this)
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
    [this.heroes, this.teams, this.jobs, this.rules] = await Promise.all([
      api.get("/api/heroes"),
      api.get("/api/teams"),
      api.get("/api/jobs"),
      api.get("/api/team-rules"),
    ]);
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

  /** Makes a team; its heroes ($heroes: [{name, job}]) may come with it or be added later, on its page. Returns the team. */
  async saveTeam(name, heroes = []) {
    const team = await api.post("/api/teams", { name, heroes });
    this.teamId = team.id;
    await this.load();
    return team;
  }

  async renameTeam(id, name) {
    await api.post(`/api/teams/${id}/rename`, { name });
    await this.load();
  }

  /** Deletes the team and its heroes. */
  async disbandTeam(id) {
    await api.post(`/api/teams/${id}/delete`);
    await this.load();
  }

  async addHero(teamId, name, job) {
    await api.post(`/api/teams/${teamId}/heroes`, { name, job });
    await this.load();
  }

  async renameHero(id, name) {
    await api.post(`/api/heroes/${id}/rename`, { name });
    await this.load();
  }

  async removeHero(teamId, heroId) {
    await api.post(`/api/teams/${teamId}/heroes/${heroId}/delete`);
    await this.load();
  }

  async replaceHero(teamId, heroId, name, job) {
    await api.post(`/api/teams/${teamId}/heroes/${heroId}/replace`, { name, job });
    await this.load();
  }

  async moveHero(heroId, teamId) {
    await api.post(`/api/heroes/${heroId}/move`, { team_id: teamId });
    await this.load();
  }

  async swapHeroes(heroId, withHeroId) {
    await api.post("/api/heroes/swap", { hero_id: heroId, with_hero_id: withHeroId });
    await this.load();
  }
}

// Where a fight scene gets its fight: the calls it makes and the socket it listens on. The player's is the logged-in one (the login's cookie
// and CSRF token ride along); a guest who commands monsters without an account has its own, which sends its token instead. The scene and its
// menu ask the source and know nothing of which it is. A source that can't do something (a guest has no play time to count) does nothing.
// A third plays a finished fight back from what the server stored (the fight pages): it has `replay: true`, commands nothing and hands the rounds over itself.

import * as api from "./api.js";
import { FightLink } from "./fight-link.js";

/** The logged-in player's fight. */
export const playerSource = {
  view: (id) => api.get(`/api/fights/${id}`),
  command: (id, body) => api.post(`/api/fights/${id}/commands`, body),
  playbackDone: (id, round) => api.post(`/api/fights/${id}/playback-done`, { round }),
  back: (id) => api.post(`/api/fights/${id}/monsters/back`),
  link: (id, handlers, options) => new FightLink(id, handlers, options),
};

/** The subprotocol a guest's socket offers first; the token follows (the server answers with this one only). */
export const GUEST_PROTOCOL = "terraforma.guest";

/** The fight of the monsters a guest commands, for the guest with $token. */
export function guestSource(token) {
  const headers = { Authorization: `Bearer ${token}` };
  const path = (id) => `/api/monsters/guest/fights/${id}`;
  return {
    view: (id) => api.call("GET", path(id), undefined, headers),
    command: (id, body) => api.call("POST", `${path(id)}/commands`, body, headers),
    playbackDone: async () => {},
    back: (id) => api.call("POST", `${path(id)}/back`, {}, headers),
    link: (id, handlers, options) =>
      new FightLink(id, handlers, { ...options, path: (each) => `/ws/monsters/guest/fights/${each}`, protocols: [GUEST_PROTOCOL, token] }),
  };
}

/** The rounds of a stored fight, handed to the scene one after the other as a socket would push them. */
class ReplayLink {
  constructor(replay, handlers) {
    this.replay = replay;
    this.handlers = handlers;
    this.state = "closed";
    this.timer = null;
  }

  start() {
    this.timer = setTimeout(() => {
      this.state = "open";
      this.handlers.state?.("open");
      this.handlers.message({ type: "joined" });
      const last = this.replay.rounds.length - 1;
      this.replay.rounds.forEach((each, index) => {
        this.handlers.message({ type: "round", round: each.round, events: each.events, over: index === last && this.replay.fight.over, deadline: null });
      });
    }, 0);
  }

  stop() {
    clearTimeout(this.timer);
    this.state = "closed";
  }
}

/** A finished fight played back from $replay (what a fight-pages replay call answered: `start`, `joins`, `rounds`, and `fight` as it ended). The
 * view is the fight as it began, or just after a party joined where a round opens with one. */
export function replaySource(replay) {
  const nothing = async () => {};
  return {
    replay: true,
    view: async (_id, round) => (round && replay.joins[round]) || replay.start,
    command: nothing,
    playbackDone: nothing,
    back: nothing,
    link: (_id, handlers) => new ReplayLink(replay, handlers),
  };
}

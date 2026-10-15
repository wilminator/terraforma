// Where a fight scene gets its fight: the calls it makes and the socket it listens on. The player's is the logged-in one (the login's cookie
// and CSRF token ride along); a guest who commands monsters without an account has its own, which sends its token instead. The scene and its
// menu ask the source and know nothing of which it is. A source that can't do something (a guest has no play time to count) does nothing.

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

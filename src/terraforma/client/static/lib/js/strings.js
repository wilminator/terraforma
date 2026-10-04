// Every word the engine's pages say. A game changes any of them with shell.text.set({key: "..."}); {name} is filled in.

export const STRINGS = {
  "site.play": "Play",
  "site.account": "Account",
  "site.tagline": "A browser RPG.",
  "login.title": "Log in",
  "login.username": "Username",
  "login.password": "Password",
  "login.code": "Code from your authenticator app, or a recovery code",
  "login.submit": "Log in",
  "login.busy": "Logging in…",
  "login.retry": "Too many tries. Try again in {seconds} seconds.",
  "play.logout": "Log out",
  "play.signed_in": "Playing as {username}",
  "play.stage": "The game",
  "play.rotate": "Turn your device sideways to play.",
  "account.signed_in": "Logged in as {username}.",
  "account.logout": "Log out",
  "problem.module": "Part of this game could not be loaded ({file}).",
};

/** The page's text: the engine's, with the game's changes on top. */
export class Text {
  #changes = new Map();

  set(changes) {
    for (const [key, value] of Object.entries(changes)) this.#changes.set(key, String(value));
  }

  get(key, values = {}) {
    const template = this.#changes.get(key) ?? STRINGS[key] ?? key;
    return template.replace(/\{(\w+)\}/g, (whole, name) => (name in values ? String(values[name]) : whole));
  }
}

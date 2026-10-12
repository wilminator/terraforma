// The page's line to one fight: the fight socket, kept open while the fight runs. The server pushes what it stored: "joined" when the
// socket is accepted, "committed" when a fighter has chosen (not what), "round" with the round's events when it has played, and
// "party_joined" when another party joined. The page only listens (commands are calls). If the socket drops, the link tries again a few
// times and tells the page each time it is open again, so the page can ask the server what it missed.

const RETRY_MS = 1000;
const TRIES = 5;

export class FightLink {
  /** $handlers: { message(object), state("open" | "retrying" | "lost") }. */
  constructor(id, handlers, { socket = (url) => new WebSocket(url), retryMs = RETRY_MS } = {}) {
    this.id = id;
    this.handlers = handlers;
    this.state = "closed";
    this.#open = socket;
    this.#retryMs = retryMs;
  }

  #open;
  #retryMs;
  #socket = null;
  #tries = 0;
  #stopped = false;
  #timer = null;

  start() {
    this.#stopped = false;
    this.#connect();
  }

  stop() {
    this.#stopped = true;
    clearTimeout(this.#timer);
    this.#socket?.close();
    this.#socket = null;
    this.#set("closed");
  }

  #url() {
    return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/fights/${this.id}`;
  }

  #set(state) {
    this.state = state;
    this.handlers.state?.(state);
  }

  #connect() {
    const socket = this.#open(this.#url());
    this.#socket = socket;
    socket.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return; // (not ours)
      }
      if (message.type === "joined") {
        this.#tries = 0;
        this.#set("open");
      }
      this.handlers.message(message);
    };
    socket.onclose = () => {
      if (this.#stopped || this.#socket !== socket) return;
      if (this.#tries >= TRIES) {
        this.#set("lost");
        return;
      }
      this.#tries += 1;
      this.#set("retrying");
      this.#timer = setTimeout(() => this.#connect(), this.#retryMs * this.#tries);
    };
  }
}

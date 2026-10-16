// Force disuse: when the player signs in with a hero that holds something an admin has made disused (a job, an ability or an item), or has
// something set aside that can come back, the hero goes through a scene before anything else. The server runs it (``/api/heroes/{id}/disuse``,
// a dialog the game wrote); this draws it over the whole page. The default scene is an all-black screen, so the overlay is black and shows
// the words, Next, and the choices. A scene the game stages with ``scene,name`` sets ``data-scene="name"`` on the overlay (style it) and
// emits the shell event "scene" with { name, host, heroId }, where $host is an element a game's module may draw into.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

/** Goes through the roster's heroes one by one and runs the scene of each that has something to settle. */
export async function settleHeroes(shell, roster, host) {
  let ran = false;
  for (const hero of roster.heroes) {
    const status = await api.get(`/api/heroes/${hero.id}/disuse`);
    if (status.pending || status.talking || status.restorable.length) {
      await scene(shell, host, hero, status);
      ran = true;
    }
  }
  if (ran) await roster.load(); // (a new class, abilities and items put away or back)
}

/** One hero's scene, shown until the player closes it at its end. */
function scene(shell, host, hero, status) {
  const t = (key, values) => shell.text.get(key, values);
  return new Promise((done) => {
    const lines = [];
    let frame = status.talking ? { events: [], prompt: status.prompt, ended: false, pending: status.pending } : null; // (a reload keeps the scene on the server)
    const problem = h("p", { class: "problem", role: "alert" });
    const words = h("div", { class: "transcript", "aria-live": "polite" });
    const controls = h("div", { class: "scene-controls" });
    const stage = h("div", { class: "scene-stage", id: "disuse-stage" });
    const screen = h("div", { class: "scene", id: "disuse-scene", role: "dialog", "aria-modal": "true", "aria-label": t("disuse.title", { hero: hero.name }), "data-scene": "black" }, stage, words, controls, problem);
    host.append(screen);

    function take(next) {
      frame = next;
      for (const event of next.events ?? []) {
        if (event.type === "text") lines.push(event.text);
        if (event.type === "scene") {
          screen.dataset.scene = event.name;
          shell.emit("scene", { name: event.name, host: stage, heroId: hero.id });
        }
      }
      const back = next.restored;
      if (back && (back.abilities.length || back.items.length)) {
        const names = [...back.abilities, ...back.items.map((item) => (item.qty > 1 ? `${item.name} (x${item.qty})` : item.name))];
        lines.push(t("disuse.restored", { names: names.join(", ") }));
      }
      draw();
    }

    function close() {
      screen.remove();
      done();
    }

    function draw() {
      replace(words, lines.map((line) => h("p", {}, line)));
      const prompt = frame?.prompt;
      if (frame === null || (!frame.ended && !prompt)) {
        replace(controls, h("button", { type: "button", id: "disuse-close", onclick: close }, t("disuse.close")));
      } else if (frame.ended) {
        replace(controls, frame.pending ? h("p", { class: "muted", id: "disuse-pending" }, t("disuse.pending", { hero: hero.name })) : null, h("button", { type: "button", class: "primary", id: "disuse-close", onclick: close }, t("disuse.close")));
      } else if (prompt.type === "choice") {
        replace(
          controls,
          h("div", { class: "choices", role: "group", "aria-label": t("dialog.choices") }, prompt.options.map((option, index) => h("button", { type: "button", "data-choice": String(index), onclick: () => attempt(shell, problem, () => go(index)) }, option.text))),
          prompt.accepts.cancel ? h("button", { type: "button", class: "quiet", id: "disuse-cancel", onclick: () => attempt(shell, problem, () => go(null)) }, t("dialog.cancel")) : null,
        );
      } else {
        replace(controls, h("button", { type: "button", class: "primary", id: "disuse-next", onclick: () => attempt(shell, problem, () => go(null)) }, t("dialog.next")));
      }
    }

    async function go(choice) {
      take(await api.post(`/api/heroes/${hero.id}/disuse/next`, choice === null ? {} : { choice }));
    }

    draw();
    if (frame === null) attempt(shell, problem, async () => take(await api.post(`/api/heroes/${hero.id}/disuse/start`)));
  });
}

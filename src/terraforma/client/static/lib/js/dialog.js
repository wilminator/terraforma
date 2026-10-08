// The dialog panel: what the NPC the hero is talking to has said, and what it asks next: Next, an answer to pick (a question, a
// switch, the inn's Yes or No), or an activity to run (the shop, or a screen a game added with shell.activities.add).

import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export function dialogPanel(shell, roster) {
  const t = (key, values) => shell.text.get(key, values);
  const convo = shell.conversation;
  const problem = h("p", { class: "problem", role: "alert" });
  const body = h("div", { id: "dialog-body" });

  function controls() {
    const prompt = convo.prompt;
    if (convo.ended || !prompt) {
      return [h("p", { class: "muted", id: "dialog-over" }, t("dialog.over")), h("button", { type: "button", id: "dialog-close", onclick: () => convo.close() }, t("dialog.close"))];
    }
    if (prompt.type === "ack") {
      return [h("button", { type: "button", class: "primary", id: "dialog-next", onclick: () => attempt(shell, problem, () => convo.next()) }, t("dialog.next"))];
    }
    if (prompt.type === "choice") {
      return [
        h(
          "div",
          { class: "choices", role: "group", "aria-label": t("dialog.choices") },
          prompt.options.map((option, index) => h("button", { type: "button", "data-choice": String(index), onclick: () => attempt(shell, problem, () => convo.next(index)) }, option.text)),
        ),
        prompt.accepts.cancel ? h("button", { type: "button", class: "quiet", id: "dialog-cancel", onclick: () => attempt(shell, problem, () => convo.next()) }, t("dialog.cancel")) : null,
      ];
    }
    const build = shell.activities.get(prompt.command);
    if (!build) {
      return [h("p", { class: "muted" }, t("dialog.no_screen", { command: prompt.command })), h("button", { type: "button", id: "dialog-next", onclick: () => attempt(shell, problem, () => convo.next()) }, t("dialog.next"))];
    }
    return [build({ shell, roster, heroId: convo.heroId, prompt, finish: () => convo.next(), problem })];
  }

  function draw() {
    if (!convo.talking) {
      replace(body, h("p", { class: "muted", id: "dialog-none" }, t("dialog.none")));
      return;
    }
    replace(
      body,
      h("h3", { id: "dialog-speaker" }, convo.speaker),
      h("div", { class: "transcript", "aria-live": "polite" }, convo.lines.map((line) => h("p", {}, line))),
      ...controls(),
      convo.prompt ? h("button", { type: "button", class: "quiet", id: "dialog-leave", onclick: () => attempt(shell, problem, () => convo.leave()) }, t("dialog.leave")) : null,
    );
  }

  convo.onChange(draw);
  draw();

  const element = h("div", { class: "panel", id: "panel-dialog" }, h("h2", {}, t("panel.dialog")), body, problem);
  return {
    element,
    // (a conversation survives a reload on the server: pick it up when the panel is opened)
    show: () => (roster.heroId === null ? null : attempt(shell, problem, () => convo.resume(roster.heroId))),
  };
}

// A team's own page (private: only its player sees it): its name, its heroes and the forms that fill, change and remove them.
// A regular page, for any screen and orientation; the game client's Teams panel links here.

import { h, replace } from "./dom.js";
import { problemText } from "./forms.js";
import { Roster } from "./roster.js";
import { Shell } from "./shell.js";
import { teamEditor } from "./team-editor.js";

const shell = new Shell("team");
const main = document.getElementById("app");
const t = (key, values) => shell.text.get(key, values);
const id = Number(location.pathname.split("/").filter(Boolean).pop());
const back = h("p", {}, h("a", { href: "/play/", id: "back-to-game" }, t("team.back")));

await shell.start();
if (!(await shell.restore())) {
  location.replace("/account/?next=play"); // one login; it sends the player back to the game
} else {
  const roster = new Roster();
  try {
    await roster.load();
    const team = roster.team(id);
    document.title = `${team?.name ?? t("team.missing_title")} · ${shell.game.game ?? "TerraForma"}`;
    if (team) replace(main, back, teamEditor(shell, roster, id, () => location.assign("/play/")));
    else replace(main, h("h1", {}, t("team.missing_title")), h("p", { role: "alert" }, t("team.missing")), back);
  } catch (error) {
    replace(main, h("p", { class: "problem", role: "alert" }, problemText(shell, error)), back);
  }
  if (shell.problems.length) main.append(h("p", { class: "problem", role: "alert" }, shell.problems.join(" ")));
}

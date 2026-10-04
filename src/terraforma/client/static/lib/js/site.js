// The marketing site's page: the game's name and a way in. Game modules may add to it (the game's lists and news).

import { Shell } from "./shell.js";

const shell = new Shell("site");
await shell.start();
document.title = shell.game.game ?? "TerraForma";

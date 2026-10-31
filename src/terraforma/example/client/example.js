// The example game's browser module: the smallest use of the hooks. A game's own module does the same, with its own words and screens.

export default function register(shell) {
  shell.text.set({
    "site.tagline": "The TerraForma example game: a tiny world to try the engine in.",
  });
  // The sounds: which of the game's files (the assets folder, audio/) go with what the engine says happens (see audio.js).
  shell.audio.add({
    "panel.open": "audio/sfx/panel_open.mp3",
    "panel.close": "audio/sfx/panel_close.mp3",
    "menu.confirm": "audio/sfx/menu_confirm.mp3",
    "menu.cancel": "audio/sfx/menu_cancel.mp3",
    "fight.start": "audio/sfx/fight_start.mp3",
    "fight.act": "audio/sfx/attack.mp3",
    "fight.damage": "audio/sfx/hit.mp3",
    "fight.miss": "audio/sfx/miss.mp3",
    "fight.heal": "audio/sfx/heal.mp3",
    "fight.died": "audio/sfx/died.mp3",
    "fight.level": "audio/sfx/level_up.mp3",
    "fight.victory": "audio/sfx/victory.mp3",
    "fight.defeat": "audio/sfx/defeat.mp3",
  });
  shell.audio.addMusic({ map: "audio/music/map.mp3", fight: "audio/music/combat.mp3" });
  shell.on("ready", () => {
    document.documentElement.dataset.exampleReady = "true";
  });
}

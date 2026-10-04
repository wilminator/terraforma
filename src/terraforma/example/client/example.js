// The example game's browser module: the smallest use of the hooks. A game's own module does the same, with its own words and screens.

export default function register(shell) {
  shell.text.set({
    "site.tagline": "The TerraForma example game: a tiny world to try the engine in.",
  });
  shell.on("ready", () => {
    document.documentElement.dataset.exampleReady = "true";
  });
}

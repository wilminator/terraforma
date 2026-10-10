# Assets of the TerraForma example game

| File | Source | License |
|---|---|---|
| `grass.svg`, `forest.svg`, `water.svg`, `sand.svg`, `stone.svg`, `floor.svg` | Drawn for this repository: plain shapes written by hand as SVG (16 by 16 tiles), not taken from any other work. Kept as the example of a plain-file picture (the engine's tests load them); the maps draw with `tiles.png`. | The engine's own: AGPL-3.0 with the module permission (`LICENSE`, `LICENSE-EXCEPTION.md`). |
| `tiles.png`, `tiles.sheet.json` | The example's tileset: 13 pictures of 32 by 32 pixels (a cave, a tower, a castle, a town, grass, hills, desert, two swamps (mid and dark green), a mountain, water, cacti and a tree), drawn by Michael Allen Wilmes (the project owner) in QBasic for an earlier game of his, not for this project, and converted to a tilesheet with `terraforma.tools.qbtiles`. | Michael Allen Wilmes's own work, which he released as open source with this repository: AGPL-3.0 with the module permission (`LICENSE`, `LICENSE-EXCEPTION.md`). |
| `crystal.png`, `crystal.alpha.png`, `crystal.sheet.json`, `crystal_red.colors.json` | Drawn for this repository: a two-frame sheet of a diamond, with its alpha sheet and a red color map, generated from hand-written pixel rules, not taken from any other work. | The engine's own: AGPL-3.0 with the module permission (`LICENSE`, `LICENSE-EXCEPTION.md`). |

Rule for anything added here: record its source and license in this table. Any license that is compatible with the AGPL is fine
(CC0, CC-BY, CC-BY-SA 4.0, MIT, BSD, OFL, GPL); never one that is unknown, non-commercial or no-derivatives.

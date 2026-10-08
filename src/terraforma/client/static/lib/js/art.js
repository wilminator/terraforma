// Pictures from the game's assets. An art reference is a file name (one image, drawn as it is) or a sheet reference
// { sheet, colors, animations } (the same object maps.json holds):
//
//   sheet       NAME.png (indexed colors), NAME.alpha.png (the 8-bit transparency, as gray) and NAME.sheet.json
//               { frame: [w, h], grid: [columns, rows], frames, palette: ["#rrggbb", ...], alpha: true }
//   colors      optional NAME.colors.json, a color map { "#3050a0": "#a03030" }
//   animations  optional NAME.anim.json (played by a later slice; read here only to be handed on)
//
// shell.art.load(reference) resolves to { source, frame, grid, frames, palette, animations }: $source is something
// drawImage takes (a canvas for a sheet, an image for a file name), the rest is null for a plain file. The combined,
// recolored sheet is cached per sheet and color map, so many tiles and heroes share one canvas.
//
// Recoloring: each pixel is first snapped to the nearest color of the sheet's palette (so a color the picture only
// nearly has still matches), then looked up in the color map; colors the map doesn't list stay as they are. It runs
// before the transparency is added, so soft edges keep their alpha.

/** "#3050a0" -> [48, 80, 160]. */
export function parseHex(text) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(text).trim());
  if (!match) throw new Error(`"${text}" is not a color like #3050a0`);
  const value = parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** The index of the color in $colors (a list of [r, g, b]) nearest to $rgb; the first wins a tie. */
export function nearest(rgb, colors) {
  let best = -1;
  let bestDistance = Infinity;
  colors.forEach((color, index) => {
    const distance = (color[0] - rgb[0]) ** 2 + (color[1] - rgb[1]) ** 2 + (color[2] - rgb[2]) ** 2;
    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}

/** Recolors $pixels (ImageData, in place) by $map ({hex: hex}); $palette (hex list) is what pixels are snapped to first. */
export function recolor(pixels, palette, map) {
  const swaps = new Map(Object.entries(map).map(([from, to]) => [parseHex(from).join(","), parseHex(to)]));
  const colors = palette.map(parseHex);
  const data = pixels.data;
  const memo = new Map();
  for (let at = 0; at < data.length; at += 4) {
    const key = `${data[at]},${data[at + 1]},${data[at + 2]}`;
    let target = memo.get(key);
    if (target === undefined) {
      const snapped = colors.length ? colors[nearest([data[at], data[at + 1], data[at + 2]], colors)] : [data[at], data[at + 1], data[at + 2]];
      target = swaps.get(snapped.join(",")) ?? null;
      memo.set(key, target);
    }
    if (target) [data[at], data[at + 1], data[at + 2]] = target;
  }
  return pixels;
}

/** Sets the alpha of $pixels (ImageData, in place) from the gray of $alpha (ImageData of the same size). */
export function applyAlpha(pixels, alpha) {
  if (pixels.data.length !== alpha.data.length) throw new Error("the alpha sheet is not the size of the color sheet");
  for (let at = 3; at < pixels.data.length; at += 4) pixels.data[at] = alpha.data[at - 3];
  return pixels;
}

/** Where frame $index sits on a sheet described by $info: { x, y, w, h }. */
export function frameRect(info, index) {
  const [w, h] = info.frame;
  const columns = info.grid[0];
  return { x: (index % columns) * w, y: Math.floor(index / columns) * h, w, h };
}

function canvasOf(image) {
  const width = image.naturalWidth ?? image.width;
  const height = image.naturalHeight ?? image.height;
  const canvas = Object.assign(document.createElement("canvas"), { width, height });
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0);
  return { canvas, context };
}

async function fetchJson(url) {
  const answer = await fetch(url, { credentials: "same-origin" });
  if (!answer.ok) throw new Error(`${url}: ${answer.status}`);
  return answer.json();
}

async function loadImage(url) {
  const image = new Image();
  image.src = url;
  await image.decode();
  return image;
}

export class Art {
  #assetUrl;
  #fetchJson;
  #loadImage;
  #cache = new Map();

  /** $assetUrl(name) is the URL of an asset; $loaders ({ fetchJson, loadImage }) replace the browser's, for tests. */
  constructor(assetUrl, loaders = {}) {
    this.#assetUrl = assetUrl;
    this.#fetchJson = loaders.fetchJson ?? fetchJson;
    this.#loadImage = loaders.loadImage ?? loadImage;
  }

  /** Loads a reference (file name or { sheet, colors, animations }); the same reference gives the same answer. */
  load(reference) {
    const ref = typeof reference === "string" ? { file: reference } : reference;
    const key = JSON.stringify([ref.file ?? null, ref.sheet ?? null, ref.colors ?? null]);
    if (!this.#cache.has(key)) {
      const loading = ref.file ? this.#file(ref.file) : this.#sheet(ref);
      loading.catch(() => this.#cache.delete(key));
      this.#cache.set(key, loading);
    }
    return this.#cache.get(key);
  }

  async #file(name) {
    const source = await this.#loadImage(this.#assetUrl(name));
    return { source, frame: null, grid: null, frames: null, palette: null, animations: null };
  }

  async #sheet({ sheet, colors = null, animations = null }) {
    const info = await this.#fetchJson(this.#assetUrl(`${sheet}.sheet.json`));
    const hasAlpha = info.alpha !== false;
    const [colorImage, alphaImage, map] = await Promise.all([
      this.#loadImage(this.#assetUrl(`${sheet}.png`)),
      hasAlpha ? this.#loadImage(this.#assetUrl(`${sheet}.alpha.png`)) : null,
      colors ? this.#fetchJson(this.#assetUrl(`${colors}.colors.json`)) : null,
    ]);
    const { canvas, context } = canvasOf(colorImage);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
    if (map) recolor(pixels, info.palette ?? [], map);
    if (alphaImage) applyAlpha(pixels, canvasOf(alphaImage).context.getImageData(0, 0, canvas.width, canvas.height));
    context.putImageData(pixels, 0, 0);
    return {
      source: canvas, frame: info.frame, grid: info.grid, frames: info.frames ?? info.grid[0] * info.grid[1],
      palette: info.palette ?? [], animations,
    };
  }
}

// The stage: a picture area with a fixed design resolution, shown at the largest whole-number scale that fits its frame
// (so pixel art stays sharp). The tiles it shows are chosen by the screen, in the map slice; this is the frame around them.

export const DESIGN_WIDTH = 480;
export const DESIGN_HEIGHT = 270;

/** The whole-number scale ($width by $height available) lets the design resolution fill; never less than 1. */
export function scaleFor(width, height) {
  return Math.max(1, Math.floor(Math.min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT)));
}

/** Sizes $stage (holding $canvas, the design-resolution picture) inside $frame, now and whenever the frame changes. */
export function fitStage(frame, stage, canvas) {
  canvas.style.width = `${DESIGN_WIDTH}px`;
  canvas.style.height = `${DESIGN_HEIGHT}px`;
  const fit = () => {
    const scale = scaleFor(frame.clientWidth, frame.clientHeight);
    stage.dataset.scale = String(scale);
    stage.style.width = `${DESIGN_WIDTH * scale}px`;
    stage.style.height = `${DESIGN_HEIGHT * scale}px`;
    canvas.style.transform = `scale(${scale})`;
  };
  const observer = new ResizeObserver(fit);
  observer.observe(frame);
  fit();
  return () => observer.disconnect();
}

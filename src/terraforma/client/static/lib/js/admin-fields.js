// Turns what an admin types into what the admin calls take, and back. A field the admin got wrong throws an ApiError (status 0), so the
// page shows it the way it shows a refusal from the server; the server still checks everything again.

import { ApiError } from "./api.js";
import { h } from "./dom.js";

const fail = (message) => new ApiError(0, message);

/** "Strength: 12" lines to { Strength: 12 }. Blank lines are skipped; anything else that is not a name, a colon and a whole number is refused. */
export function parseNumbers(text, shell) {
  const found = {};
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const match = /^\s*([^:]+?)\s*:\s*(-?\d+)\s*$/.exec(line);
    if (!match) throw fail(shell.text.get("admin.bad_number_line", { line: line.trim() }));
    found[match[1]] = Number(match[2]);
  }
  return found;
}

export const formatNumbers = (values) => Object.entries(values ?? {}).map(([name, number]) => `${name}: ${number}`).join("\n");

/** Keys separated by commas, spaces or lines to a list (a key is never listed twice). */
export const parseKeys = (text) => [...new Set(text.split(/[\s,]+/).filter(Boolean))];

export const formatKeys = (keys) => (keys ?? []).join(", ");

/** JSON text for an object (the game's own description of an event) to the object; empty text is an empty object. */
export function parseObject(text, shell) {
  if (!text.trim()) return {};
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw fail(shell.text.get("admin.bad_json"));
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw fail(shell.text.get("admin.bad_json"));
  return value;
}

export const formatObject = (value) => JSON.stringify(value ?? {}, null, 2);

/** A whole number from a box that may be left empty ($blank is what empty means); a bad one is refused naming the box. */
export function parseWhole(text, label, shell, blank = null) {
  const trimmed = text.trim();
  if (!trimmed) return blank;
  if (!/^\d+$/.test(trimmed)) throw fail(shell.text.get("admin.bad_whole", { label }));
  return Number(trimmed);
}

const pad = (number) => String(number).padStart(2, "0");

/** The value of a datetime-local box (the admin's own clock) to seconds since 1970. */
export function toSeconds(value, label, shell) {
  const moment = new Date(value);
  if (!value || Number.isNaN(moment.getTime())) throw fail(shell.text.get("admin.bad_time", { label }));
  return Math.floor(moment.getTime() / 1000);
}

/** Seconds since 1970 to what a datetime-local box takes. */
export function toBox(seconds) {
  const moment = new Date(seconds * 1000);
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}T${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

/** Seconds since 1970 to a short time to read, in the admin's own clock. */
export function showTime(seconds) {
  const moment = new Date(seconds * 1000);
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

/** A labelled box for several lines of text: { row, input }. */
export function area(id, label, attributes = {}) {
  const input = h("textarea", { id, name: id, rows: "4", ...attributes });
  return { row: h("div", { class: "field" }, h("label", { for: id }, label), input), input };
}

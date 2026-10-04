// Builds DOM the safe way: text goes in as text, never as markup.

/** h("button", {class: "x", onclick: fn}, "Label", child...) */
export function h(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on") && typeof value === "function") {
      element.addEventListener(name.slice(2), value);
    } else if (value === true) {
      element.setAttribute(name, "");
    } else {
      element.setAttribute(name, value);
    }
  }
  element.append(...children.flat().filter((child) => child !== null && child !== undefined && child !== false));
  return element;
}

/** Empties $element and puts $children in. */
export function replace(element, ...children) {
  element.replaceChildren(...children.flat().filter(Boolean));
}

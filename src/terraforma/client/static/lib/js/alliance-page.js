// The alliance's public page, written by a role that may speak for it: make the page, write what it says, give it a new address.

import * as api from "./api.js";
import { h, replace } from "./dom.js";
import { attempt } from "./forms.js";

export async function pageCard(shell, ctx) {
  const t = (key, values) => shell.text.get(key, values);
  const base = `/api/alliances/${ctx.id}/profile`;
  const box = h("section", { class: "card", id: "alliance-page" });
  const problem = h("p", { class: "problem", role: "alert" });
  let view = await api.get(base);

  /** Runs a call that answers with the whole page, draws it again, then says $doneKey in the note. */
  const change = (doneKey, work) => attempt(shell, problem, async () => {
    view = await work();
    draw();
    if (doneKey) box.querySelector("#alliance-bio-note").textContent = t(doneKey);
  });

  function draw() {
    const bio = h("textarea", { id: "alliance-bio", rows: "4", maxlength: "500" }, view.bio);
    replace(
      box,
      h("h2", {}, t("alliance.page")),
      view.token
        ? [
            h("p", { id: "alliance-state" }, t("alliance.page_public")),
            h("p", { id: "alliance-address" }, `${t("alliance.page_address")} `, h("a", { href: `/p/alliance/${view.token}` }, `${location.origin}/p/alliance/${view.token}`)),
            h("button", { type: "button", onclick: () => change("alliance.page_renewed", () => api.post(`${base}/token`)) }, t("alliance.page_renew")),
          ]
        : [
            h("p", { id: "alliance-state" }, t("alliance.page_none")),
            h("button", { type: "button", class: "primary", onclick: () => change(null, () => api.post(`${base}/token`)) }, t("alliance.page_make")),
          ],
      h(
        "form",
        {
          onsubmit: (event) => {
            event.preventDefault();
            change("alliance.page_bio_saved", () => api.put(`${base}/bio`, { bio: bio.value }));
          },
        },
        h("div", { class: "field" }, h("label", { for: "alliance-bio" }, t("alliance.page_bio")), bio),
        h("p", { class: "note", role: "status", id: "alliance-bio-note" }),
        h("button", { type: "submit", class: "primary" }, t("alliance.page_bio_save")),
      ),
      problem,
    );
  }

  draw();
  return box;
}

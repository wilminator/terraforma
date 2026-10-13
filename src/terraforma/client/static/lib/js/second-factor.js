// Asking for a second factor, in two shapes:
//
// * secondFactorRows: the rows of the login step (a code field, a button for the security key, "remember this browser"), shared
//   by the login form and the step after a provider sign-in.
// * proofField: the field above a change to the account (email address, linking, two-factor settings): a code, or, left empty,
//   the security key.

import { ApiError, post } from "./api.js";
import { h } from "./dom.js";
import { field } from "./forms.js";
import { getAssertion, supported } from "./webauthn.js";

const CODE = { autocomplete: "one-time-code", maxlength: 32, inputmode: "text" };

/**
 * The rows that ask for the second factor at login. $offer is what the server answered ({ methods, webauthn, remember_days });
 * $submit(extra) sends the login again with the code or the key's answer in $extra; $fresh() asks the server for a new offer
 * (a key's challenge works once). Returns { rows, remember(), focus() }.
 */
export function secondFactorRows(shell, { offer, prefix, submit, fresh }) {
  const t = (key, values) => shell.text.get(key, values);
  const code = field(`${prefix}-code`, t("login.code"), { ...CODE, required: !offer.webauthn });
  const remember = h("input", { type: "checkbox", id: `${prefix}-remember` });
  const rows = [code.row];
  let options = offer.webauthn;
  if (options && supported()) {
    const problem = h("p", { class: "problem", role: "alert" });
    rows.unshift(
      h("p", {}, h("button", {
        type: "button",
        id: `${prefix}-key`,
        onclick: async () => {
          problem.textContent = "";
          try {
            // A challenge works once: take the one on offer, or ask for another.
            const asked = options ?? (await fresh()).webauthn;
            options = null;
            await submit({ assertion: await getAssertion(shell, asked) });
          } catch (error) {
            problem.textContent = error.message;
          }
        },
      }, t("login.use_key"))),
      problem,
    );
  }
  if (offer.remember_days > 0) rows.push(h("label", { class: "check" }, remember, t("login.remember", { days: offer.remember_days })));
  return { rows, remember: () => remember.checked, code: code.input, focus: () => (options && supported() ? document.getElementById(`${prefix}-key`) : code.input)?.focus() };
}

/**
 * The proof a change to the account needs, when two-factor login is on: a code or recovery code typed in, or, with the field
 * empty, a security key's answer. $status is the answer of /api/2fa. Returns { row, collect() }; collect() resolves to the fields
 * to add to the call ({} when two-factor login is off).
 */
export function proofField(shell, status, id) {
  const t = (key, values) => shell.text.get(key, values);
  const hasKeys = status.keys.length > 0;
  const code = field(id, t(hasKeys ? "twofa.code_or_key" : "twofa.code"), { ...CODE, required: status.enabled && !hasKeys });
  code.row.hidden = !status.enabled;
  return {
    row: code.row,
    input: code.input,
    async collect() {
      if (!status.enabled) return {};
      const typed = code.input.value.trim();
      if (typed) return { code: typed };
      if (!hasKeys) throw new ApiError(0, t("twofa.need_code"));
      const { webauthn } = await post("/api/2fa/challenge");
      return { assertion: await getAssertion(shell, webauthn) };
    },
  };
}

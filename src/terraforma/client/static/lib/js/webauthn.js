// Security keys and passkeys (WebAuthn) in the browser: the two ceremonies, and the translation between the server's JSON and the
// binary the browser wants. Browsers that know the JSON form (PublicKeyCredential.parseCreationOptionsFromJSON) are used as they are.

import { ApiError } from "./api.js";

export const supported = () => typeof window.PublicKeyCredential === "function" && Boolean(navigator.credentials);

const fromBase64Url = (text) => {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0)).buffer;
};

const toBase64Url = (buffer) => {
  if (buffer === null || buffer === undefined) return null;
  let text = "";
  for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

const withBinary = (descriptor) => ({ ...descriptor, id: fromBase64Url(descriptor.id) });

/** The server's creation options as the browser's own. */
function creationOptions(options) {
  if (PublicKeyCredential.parseCreationOptionsFromJSON) return PublicKeyCredential.parseCreationOptionsFromJSON(options);
  return {
    ...options,
    challenge: fromBase64Url(options.challenge),
    user: { ...options.user, id: fromBase64Url(options.user.id) },
    excludeCredentials: (options.excludeCredentials ?? []).map(withBinary),
  };
}

function requestOptions(options) {
  if (PublicKeyCredential.parseRequestOptionsFromJSON) return PublicKeyCredential.parseRequestOptionsFromJSON(options);
  return { ...options, challenge: fromBase64Url(options.challenge), allowCredentials: (options.allowCredentials ?? []).map(withBinary) };
}

/** What the browser made, as the JSON text the server reads. */
function asJson(credential) {
  if (credential.toJSON) return JSON.stringify(credential.toJSON());
  const response = credential.response;
  const answer = { clientDataJSON: toBase64Url(response.clientDataJSON) };
  if (response.attestationObject) {
    answer.attestationObject = toBase64Url(response.attestationObject);
    answer.transports = response.getTransports ? response.getTransports() : [];
  } else {
    answer.authenticatorData = toBase64Url(response.authenticatorData);
    answer.signature = toBase64Url(response.signature);
    answer.userHandle = toBase64Url(response.userHandle);
  }
  return JSON.stringify({
    id: credential.id,
    rawId: toBase64Url(credential.rawId),
    type: credential.type,
    response: answer,
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment,
  });
}

/** Runs a ceremony; the browser's refusals (cancelled, timed out, no such key) become the page's usual problem text. */
async function ceremony(shell, work) {
  if (!supported()) throw new ApiError(0, shell.text.get("key.unsupported"));
  try {
    return asJson(await work());
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, shell.text.get(error?.name === "InvalidStateError" ? "key.already" : "key.failed"));
  }
}

/** Asks the browser to make a key from the server's options. Returns the JSON text to send back. */
export const createCredential = (shell, options) => ceremony(shell, () => navigator.credentials.create({ publicKey: creationOptions(options) }));

/** Asks the browser to sign with one of the account's keys. Returns the JSON text to send back. */
export const getAssertion = (shell, options) => ceremony(shell, () => navigator.credentials.get({ publicKey: requestOptions(options) }));

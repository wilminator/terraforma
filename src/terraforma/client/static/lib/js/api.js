// The server's calls from the browser. A call that changes something sends the CSRF token the login gave.

export class ApiError extends Error {
  constructor(status, message, retryAfter = null) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

let csrfToken = null;

export function setCsrfToken(token) {
  csrfToken = token;
}

/** The text of a failed call's answer: FastAPI sends a string, or a list of problems for a body it refused. */
function detailOf(body, status) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((problem) => problem.msg).join("; ");
  return `The server answered ${status}.`;
}

/** Calls the server and returns the answer's JSON (null when there is none). Throws ApiError when it says no. */
export async function call(method, path, body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "The server can't be reached. Check your connection and try again.");
  }
  const isJson = (response.headers.get("Content-Type") || "").includes("json");
  const answer = isJson ? await response.json() : null;
  if (!response.ok) {
    throw new ApiError(response.status, detailOf(answer, response.status), Number(response.headers.get("Retry-After")) || null);
  }
  return answer;
}

export const get = (path) => call("GET", path);
export const post = (path, body = {}) => call("POST", path, body);

/**
 * Shared plumbing for the API clients.
 *
 * Every area talks to the same FastAPI backend and gets its errors back in
 * the same envelope, so reading that envelope belongs in one place rather
 * than being written once per module.
 */

export const API_BASE_URL = "http://localhost:8000";

/**
 * The sentence to show the user for a failed response.
 *
 * FastAPI puts plain strings in `detail` for our own HTTPExceptions (e.g. the
 * duplicate-file message) and an array of objects for validation errors.
 * Surface the string as-is so the user reads a sentence, not JSON.
 */
export async function errorMessage(res, fallback) {
  const body = await res.json().catch(() => ({}));
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => d.msg).filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return detail ? JSON.stringify(detail) : fallback;
}

/** GET/POST/PUT returning JSON, or throw with the backend's own message. */
export async function requestJson(path, { fallback, ...options } = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json", ...options.headers } : options.headers,
  });
  if (!res.ok) throw new Error(await errorMessage(res, fallback));
  return res.json();
}

/** A request with nothing to return, e.g. a 204 from DELETE. */
export async function requestNoContent(path, { fallback, ...options } = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, options);
  if (!res.ok) throw new Error(await errorMessage(res, fallback));
}

/**
 * Query string from a filter object.
 *
 * An array value becomes one repeated parameter per entry, which is how
 * FastAPI reads a `List[str]` — `tags=civil&tags=roads`, not `tags=civil,roads`.
 * Empty values are dropped so the URL only carries filters actually in use.
 */
export function queryString(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      value.filter((entry) => entry !== "" && entry !== null).forEach((entry) => search.append(key, entry));
    } else {
      search.append(key, value);
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

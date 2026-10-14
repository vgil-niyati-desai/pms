// The base URL and the error-envelope reader moved to http.js when Projects
// gained its own client and needed both. Re-exported here so the modules and
// screens already importing API_BASE_URL from this file keep working.
import { API_BASE_URL, apiFetch, errorMessage, queryString, requestJson } from "./http";

export { API_BASE_URL };

export async function createDocument(formData) {
  const res = await apiFetch(`${API_BASE_URL}/documents/`, {
    method: "POST",
    body: formData, // browser sets the correct multipart Content-Type automatically
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to save entry"));
  }
  return res.json();
}

export async function updateDocument(id, formData) {
  const res = await apiFetch(`${API_BASE_URL}/documents/${id}`, {
    method: "PUT",
    body: formData, // omit document_file to keep the existing file
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to update entry"));
  }
  return res.json();
}

export async function getDocument(id) {
  const res = await apiFetch(`${API_BASE_URL}/documents/${id}`);
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to load entry"));
  }
  return res.json();
}

/**
 * Every descriptive field a document carries. PUT writes all of them on each
 * call -- one it is not sent is stored as empty -- so a partial edit has to
 * send the rest back unchanged.
 */
const DOCUMENT_FIELDS = [
  "document_type",
  "category",
  "client_name",
  "reference_number",
  "project_title",
  "contract_value",
  "document_date",
  "department",
  "submitted_by",
  "notes",
];

/**
 * Change only the named fields of a document, and optionally its file.
 *
 * A document is shared: the same row is what the Documents screen edits and
 * what a project, tender or employee record points at. An edit made from one
 * of those owners must not quietly rewrite what the others wrote, so this
 * reads the stored document first and sends every field it was not asked to
 * change back exactly as it found it. `changes` keys left undefined are
 * treated as not changing.
 */
export async function updateDocumentFields(id, changes, file = null) {
  const stored = await getDocument(id);
  const data = new FormData();
  for (const key of DOCUMENT_FIELDS) {
    const value = changes[key] !== undefined ? changes[key] : stored[key];
    data.append(key, value ?? "");
  }
  // Omitting the file is what tells the backend to keep the existing one.
  if (file) data.append("document_file", file);
  return updateDocument(id, data);
}

/**
 * The record a usage check or a delete is made from, as query parameters.
 *
 * `owner` is `{ fromProject }`, `{ fromTender }` for a tender's document
 * list, or `{ fromTender | fromEmployee, fromItem }` for one CV,
 * certification, cost item or submitted certificate inside them.
 */
function ownerQuery({ fromProject, fromTender, fromEmployee, fromItem } = {}) {
  return queryString({
    from_project: fromProject,
    from_tender: fromTender,
    from_employee: fromEmployee,
    from_item: fromItem,
  });
}

/**
 * Every project, tender and employee using a document:
 * `[{ kind, id, label, roles }]`. What a permanent delete would take it
 * away from. Given an `owner` (see ownerQuery), that owner's own use is
 * left out, leaving exactly what a delete from there would be refused over.
 */
export async function getDocumentUsage(id, owner) {
  return requestJson(`/documents/${id}/usage${ownerQuery(owner)}`, {
    fallback: "Failed to check where this document is used",
  });
}

/**
 * Permanently delete a document. Given an `owner` (see ownerQuery), the
 * server refuses (409) when anything else still uses it.
 */
export async function deleteDocument(id, owner) {
  const query = ownerQuery(owner);
  const res = await apiFetch(`${API_BASE_URL}/documents/${id}${query}`, { method: "DELETE" });
  if (!res.ok) {
    const error = new Error(await errorMessage(res, "Failed to delete entry"));
    // Kept so a caller can tell "already gone" from a real failure.
    error.status = res.status;
    throw error;
  }
}

/**
 * One page of the document log: `{ items, total, page, page_size }`, the same
 * shape every other list returns. Filters left empty are not sent.
 */
export async function listDocuments(filters = {}) {
  const query = queryString({
    q: filters.q,
    document_type: filters.document_type,
    category: filters.category,
    project_id: filters.project_id,
    sort: filters.sort,
    page: filters.page,
    page_size: filters.pageSize,
  });
  return requestJson(`/documents/${query}`, { fallback: "Failed to load entries" });
}

/** Every document type in use across the log, for filters and forms. */
export async function listDocumentTypes() {
  return requestJson("/documents/types", { fallback: "Failed to load document types" });
}

/**
 * Every category in use across the log, for the log's filter and as
 * suggestions on the forms that ask for one.
 *
 * Read from the data rather than a fixed list: a category is free text, so
 * the only list that can be right is what has already been written. Offering
 * it is what stops the same heading being retyped three ways -- the filter
 * matches exactly, so a spelling nobody reuses is a filter that finds nothing.
 */
export async function listDocumentCategories() {
  return requestJson("/documents/categories", {
    fallback: "Failed to load document categories",
  });
}

/**
 * Where the browser fetches a document's stored file.
 *
 * `inline` asks the backend to serve it for display instead of as a download,
 * which is what the preview needs â€” a PDF served as an attachment is
 * downloaded by the browser rather than rendered in the viewer. Leaving it off
 * gives the download URL, so the same helper answers both questions.
 */
export function documentFileUrl(documentId, { inline = false } = {}) {
  if (!documentId) return null;
  return `${API_BASE_URL}/documents/${documentId}/file${inline ? "?disposition=inline" : ""}`;
}

/**
 * The stored file itself, as a Blob -- or an Error carrying the backend's own
 * sentence ("File missing on server", say) when it cannot be served.
 *
 * The preview used to point an iframe or an img straight at the file URL,
 * which has no way of learning that the answer was a 404: the frame showed
 * the error JSON as text and the img a broken picture, and the Download link
 * beside them navigated the whole app to the error page. Fetching the bytes
 * first is what makes a failure readable, and the same Blob is what Download
 * saves, so a missing file is reported in place and the app stays where it
 * is. Nothing about the backend changes: it is the same inline file route.
 */
export async function fetchDocumentFile(documentId) {
  const res = await apiFetch(documentFileUrl(documentId, { inline: true }));
  if (!res.ok) throw new Error(await errorMessage(res, "Failed to load the file"));
  return res.blob();
}

/* ---- manual redaction ---------------------------------------------------
 *
 * Redaction never changes the document it reads. No endpoint below modifies
 * the entry, replaces its file, or touches a byte of it, so the original keeps
 * previewing and downloading exactly as it always has.
 *
 * Two things can be done with the copy that comes out. `createRedactedCopy`
 * generates it per request and hands it back as a download, leaving nothing on
 * the server. `saveRedactedCopy` logs it: a second file and a second entry,
 * beside the original rather than over it.
 */

/** Which file types the Redact action is offered for. */
const REDACTABLE_EXTENSIONS = ["pdf", "png", "jpg", "jpeg"];

export function canRedact(fileName) {
  const match = /\.([a-z0-9]+)$/i.exec(fileName || "");
  return match ? REDACTABLE_EXTENSIONS.includes(match[1].toLowerCase()) : false;
}

/** The page count and page sizes the selection view lays itself out from. */
export async function getRedactionSource(documentId) {
  const res = await apiFetch(`${API_BASE_URL}/documents/${documentId}/redaction/source`);
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to open this document for redaction"));
  }
  return res.json();
}

/**
 * A rendered page image to draw rectangles over.
 *
 * The normal preview hands a PDF to the browser's own viewer in an iframe,
 * which is opaque â€” nothing outside it can tell where a click landed. Asking
 * the backend for the page as an image is what makes the selection possible.
 */
export function redactionPageImageUrl(documentId, pageIndex, width) {
  const query = width ? `?width=${Math.round(width)}` : "";
  return `${API_BASE_URL}/documents/${documentId}/redaction/pages/${pageIndex}${query}`;
}

/**
 * Areas the backend thinks hold financial information, for the user to review.
 *
 * A suggestion and nothing else: this endpoint reads the stored file and
 * returns rectangles in the *same* normalised format `createRedactedCopy`
 * takes below, so a box the user accepts is posted back through the ordinary
 * redaction request with no special casing at either end. Nothing is redacted,
 * stored or changed by asking.
 *
 * Reports what it could not read as well as what it found â€” a scanned page
 * has no text layer, and coming back empty from one has to be distinguishable
 * from coming back empty from a clean one.
 */
export async function getSensitiveSuggestions(documentId) {
  const res = await apiFetch(`${API_BASE_URL}/documents/${documentId}/redaction/suggestions`);
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to scan this document for figures"));
  }
  return res.json();
}

/**
 * Generates the redacted copy and returns it as a blob plus the name to save
 * it under, which the backend supplies in Content-Disposition.
 *
 * `areas` are normalised to the page â€” x, y, width and height as fractions,
 * origin top-left â€” so the rectangles mean the same thing whatever size the
 * page was rendered at in the browser.
 */
export async function createRedactedCopy(documentId, areas) {
  const res = await apiFetch(`${API_BASE_URL}/documents/${documentId}/redacted-copy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ areas }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to generate the redacted copy"));
  }
  return { blob: await res.blob(), fileName: fileNameFromDisposition(res) };
}

/**
 * Logs the redacted copy as its own entry in the document log, and returns it.
 *
 * The same `areas` the download takes, and the copy is generated on the server
 * from the same untouched original -- the bytes are not sent back up, so what
 * gets logged is the server's own redaction and not something a client handed
 * it. The response is an ordinary document, carrying `redacted_from_id`
 * pointing at the entry it was made from.
 *
 * The original is not modified, not replaced, and not told about the copy. A
 * failure here leaves the log exactly as it was: nothing is written unless
 * both the file and its entry land.
 */
export async function saveRedactedCopy(documentId, areas) {
  const res = await apiFetch(`${API_BASE_URL}/documents/${documentId}/redacted-copy/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ areas }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to save the redacted copy to the log"));
  }
  return res.json();
}

/** The file name out of a Content-Disposition, RFC 5987 form preferred. */
function fileNameFromDisposition(res) {
  const header = res.headers.get("Content-Disposition") || "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      // A malformed header should not lose the download; fall through to the
      // plain filename, and to the default below that.
    }
  }
  const plain = /filename="([^"]*)"/i.exec(header);
  return (plain && plain[1]) || "redacted-copy";
}


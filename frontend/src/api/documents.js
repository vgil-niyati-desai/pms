// The base URL and the error-envelope reader moved to http.js when Projects
// gained its own client and needed both. Re-exported here so the modules and
// screens already importing API_BASE_URL from this file keep working.
import { API_BASE_URL, errorMessage } from "./http";

export { API_BASE_URL };

export async function createDocument(formData) {
  const res = await fetch(`${API_BASE_URL}/documents/`, {
    method: "POST",
    body: formData, // browser sets the correct multipart Content-Type automatically
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to save entry"));
  }
  return res.json();
}

export async function updateDocument(id, formData) {
  const res = await fetch(`${API_BASE_URL}/documents/${id}`, {
    method: "PUT",
    body: formData, // omit document_file to keep the existing file
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to update entry"));
  }
  return res.json();
}

export async function deleteDocument(id) {
  const res = await fetch(`${API_BASE_URL}/documents/${id}`, { method: "DELETE" });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to delete entry"));
  }
}

export async function listDocuments(filters = {}) {
  const params = new URLSearchParams();
  if (filters.document_type) params.append("document_type", filters.document_type);
  if (filters.category) params.append("category", filters.category);
  if (filters.q) params.append("q", filters.q);

  const query = params.toString();
  const res = await fetch(`${API_BASE_URL}/documents/${query ? `?${query}` : ""}`);
  if (!res.ok) {
    throw new Error("Failed to load entries");
  }
  return res.json();
}

/**
 * Where the browser fetches a document's stored file.
 *
 * `inline` asks the backend to serve it for display instead of as a download,
 * which is what the preview needs — a PDF served as an attachment is
 * downloaded by the browser rather than rendered in the viewer. Leaving it off
 * gives the download URL, so the same helper answers both questions.
 */
export function documentFileUrl(documentId, { inline = false } = {}) {
  if (!documentId) return null;
  return `${API_BASE_URL}/documents/${documentId}/file${inline ? "?disposition=inline" : ""}`;
}

/* ---- manual redaction ---------------------------------------------------
 *
 * Redaction reads the stored file and writes nothing: no endpoint below
 * changes the entry, replaces its file, or leaves a copy on the server. The
 * redacted file is generated per request and comes back as a download, so
 * the original keeps previewing and downloading exactly as it always has.
 */

/** Which file types the Redact action is offered for. */
const REDACTABLE_EXTENSIONS = ["pdf", "png", "jpg", "jpeg"];

export function canRedact(fileName) {
  const match = /\.([a-z0-9]+)$/i.exec(fileName || "");
  return match ? REDACTABLE_EXTENSIONS.includes(match[1].toLowerCase()) : false;
}

/** The page count and page sizes the selection view lays itself out from. */
export async function getRedactionSource(documentId) {
  const res = await fetch(`${API_BASE_URL}/documents/${documentId}/redaction/source`);
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to open this document for redaction"));
  }
  return res.json();
}

/**
 * A rendered page image to draw rectangles over.
 *
 * The normal preview hands a PDF to the browser's own viewer in an iframe,
 * which is opaque — nothing outside it can tell where a click landed. Asking
 * the backend for the page as an image is what makes the selection possible.
 */
export function redactionPageImageUrl(documentId, pageIndex, width) {
  const query = width ? `?width=${Math.round(width)}` : "";
  return `${API_BASE_URL}/documents/${documentId}/redaction/pages/${pageIndex}${query}`;
}

/**
 * Generates the redacted copy and returns it as a blob plus the name to save
 * it under, which the backend supplies in Content-Disposition.
 *
 * `areas` are normalised to the page — x, y, width and height as fractions,
 * origin top-left — so the rectangles mean the same thing whatever size the
 * page was rendered at in the browser.
 */
export async function createRedactedCopy(documentId, areas) {
  const res = await fetch(`${API_BASE_URL}/documents/${documentId}/redacted-copy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ areas }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Failed to generate the redacted copy"));
  }
  return { blob: await res.blob(), fileName: fileNameFromDisposition(res) };
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

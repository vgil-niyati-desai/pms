/**
 * File storage for records whose metadata lives in a browser store.
 *
 * CVs and certifications keep every field in api/employees.js, but their files
 * have to go somewhere real — localStorage cannot hold a PDF. The documents
 * endpoint is the only file storage that exists, so it is used for the bytes
 * alone: the local record owns the metadata and holds a document id pointing
 * at the file.
 *
 * That means each uploaded file also becomes a row in the document log. The
 * fields below are what those rows carry, kept honest rather than mapped onto
 * project-shaped columns:
 *
 *   document_type  "Resume CV" or "Certification"
 *   category       "Personnel"
 *   client_name    the employee the file belongs to
 *   project_title  the CV version, or the certificate name
 *   submitted_by   whoever uploaded it
 *
 * When employees get their own endpoint, this module is what gets replaced.
 */

import { createDocument, deleteDocument, updateDocumentFields } from "./documents";

export const CV_DOCUMENT_TYPE = "Resume CV";
export const CERTIFICATION_DOCUMENT_TYPE = "Certification";
export const TENDER_RECEIPT_DOCUMENT_TYPE = "Payment Receipt";
export const TENDER_CERTIFICATE_DOCUMENT_TYPE = "Tender Certificate";

/** Which area of the system each kind of attachment came from. */
const CATEGORY_FOR_TYPE = {
  [CV_DOCUMENT_TYPE]: "Personnel",
  [CERTIFICATION_DOCUMENT_TYPE]: "Personnel",
  [TENDER_RECEIPT_DOCUMENT_TYPE]: "Tendering",
  [TENDER_CERTIFICATE_DOCUMENT_TYPE]: "Tendering",
};

function buildFormData({ documentType, subjectName, title, reference, date, uploadedBy, file }) {
  const data = new FormData();
  data.append("document_type", documentType);
  data.append("category", CATEGORY_FOR_TYPE[documentType] ?? "Other");
  data.append("client_name", subjectName);
  data.append("project_title", title || "");
  data.append("reference_number", reference || "");
  data.append("document_date", date || "");
  // Empty when the drawer does not know who uploaded it -- never the literal
  // "undefined" FormData would otherwise write.
  data.append("submitted_by", uploadedBy || "");
  data.append("notes", "");
  if (file) data.append("document_file", file);
  return data;
}

/** Uploads a file and returns the pointer the local record stores. */
export async function createAttachment(details) {
  const saved = await createDocument(buildFormData(details));
  return { document_id: saved.id, file_name: saved.file_name };
}

/** Which document field each attachment detail is written to. */
const FIELD_FOR_DETAIL = {
  title: "project_title",
  reference: "reference_number",
  date: "document_date",
  uploadedBy: "submitted_by",
};

/**
 * The details that differ between two sets, for an edit to send.
 *
 * An edit only writes what the person actually changed in the drawer. Values
 * computed from the same unchanged inputs compare equal and are left out, so
 * opening a CV and saving it untouched rewrites nothing on its document.
 */
export function changedDetails(before, after) {
  const changed = {};
  for (const key of Object.keys(FIELD_FOR_DETAIL)) {
    if (after[key] !== undefined && after[key] !== before[key]) changed[key] = after[key];
  }
  return changed;
}

/**
 * Updates an existing attachment: its file if `file` is given, and only the
 * descriptive details present in `details`.
 *
 * Everything else on the document -- its type, category, who it is for, and
 * any notes, value or department written from the Documents screen -- is
 * left exactly as stored. Those are shared with every other place the
 * document appears, and an edit made here has no business rewriting them.
 * Returns null when there is nothing to write, so the caller keeps the
 * pointer it already has.
 */
export async function updateAttachment(documentId, details) {
  const changes = {};
  for (const [key, field] of Object.entries(FIELD_FOR_DETAIL)) {
    if (details[key] !== undefined) changes[field] = details[key] || "";
  }
  if (!details.file && Object.keys(changes).length === 0) return null;
  const saved = await updateDocumentFields(documentId, changes, details.file);
  return { document_id: saved.id, file_name: saved.file_name };
}

/**
 * Deletes an attachment's document. One that is already gone counts as
 * deleted: a retry after the first attempt succeeded on the server but its
 * response was lost must go on to remove the record, not stop at a 404.
 */
export async function deleteAttachment(documentId) {
  try {
    await deleteDocument(documentId);
  } catch (err) {
    if (err.status !== 404) throw err;
  }
}

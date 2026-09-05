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

import { createDocument, deleteDocument, updateDocument } from "./documents";

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
  data.append("submitted_by", uploadedBy);
  data.append("notes", "");
  if (file) data.append("document_file", file);
  return data;
}

/** Uploads a file and returns the pointer the local record stores. */
export async function createAttachment(details) {
  const saved = await createDocument(buildFormData(details));
  return { document_id: saved.id, file_name: saved.file_name };
}

/**
 * Updates an existing attachment. Omitting `file` keeps the stored file and
 * refreshes only the descriptive fields, which is what an edit that does not
 * touch the upload should do.
 */
export async function updateAttachment(documentId, details) {
  const saved = await updateDocument(documentId, buildFormData(details));
  return { document_id: saved.id, file_name: saved.file_name };
}

export async function deleteAttachment(documentId) {
  await deleteDocument(documentId);
}

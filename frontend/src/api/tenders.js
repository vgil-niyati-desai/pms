/**
 * Tender data access.
 *
 * Backed by the /tenders endpoints on the FastAPI backend, which persist to
 * MongoDB — the last of the three areas to move off its browser-local store,
 * after api/projects.js and api/employees.js. Like both, it kept its function
 * signatures and return shapes, so the screens above it did not change.
 *
 * Cost items and certificates are nested inside their tender
 * (/tenders/:id/cost-items, ...), and attached documents are linked by id
 * the way a project's are. Only the metadata travels here: uploaded receipts
 * and certificate scans go to the documents endpoint through
 * api/attachments.js, and each record keeps the document id.
 */

import { queryString, requestJson, requestNoContent } from "./http";

export const TENDER_STATUSES = [
  "Identified",
  "Preparing",
  "Submitted",
  "Won",
  "Lost",
  "Withdrawn",
];

/**
 * The statuses the "Open" view shows: tenders still being worked on. Once a
 * bid is submitted the deadline has passed for us, so it drops out.
 * The server keeps its own copy of this list (OPEN_STATUSES in
 * routers/tenders_mongo.py); the two must agree.
 */
export const OPEN_STATUSES = ["Identified", "Preparing"];

export const COST_TYPES = [
  "EMD",
  "Tender fee",
  "Processing fee",
  "DD / BG charges",
  "Other",
];

/** Field defaults, and the canonical field list for a tender record. Also
 *  the list of what the form may send — createTender and updateTender pick
 *  exactly these keys, so ids, timestamps, nested records and document
 *  links can never be overwritten by a form post. */
export const EMPTY_TENDER = {
  title: "",
  issuing_authority: "",
  reference_number: "",
  tender_type: "",
  estimated_value: "",
  emd_amount: "",
  tender_fee: "",
  published_date: "",
  submission_deadline: "",
  status: "",
  submission_mode: "",
  notes: "",
  tags: [],
};

export const EMPTY_COST_ITEM = {
  cost_type: "",
  amount: "",
  payment_mode: "",
  instrument_number: "",
  paid_on: "",
  document_id: null,
  file_name: null,
};

export const EMPTY_TENDER_CERTIFICATE = {
  name: "",
  issuing_body: "",
  certificate_number: "",
  valid_from: "",
  valid_to: "",
  notes: "",
  document_id: null,
  file_name: null,
};

/** The fields of `defaults` present in `values`, blanks filled in. */
function pick(defaults, values) {
  const payload = {};
  for (const key of Object.keys(defaults)) {
    payload[key] = values[key] ?? defaults[key];
  }
  return payload;
}

// --- tenders ------------------------------------------------------------

export async function listTenders(filters = {}) {
  const {
    q = "",
    statuses = [],
    authority = "",
    tags = [],
    tagMode = "any",
    deadlineFrom = "",
    deadlineTo = "",
    minValue = "",
    maxValue = "",
    openOnly = false,
    sort = "-updated_at",
    page = 1,
    pageSize = 10,
  } = filters;

  const query = queryString({
    q,
    statuses,
    authority,
    tags,
    tag_mode: tags.length > 1 ? tagMode : "any",
    deadline_from: deadlineFrom,
    deadline_to: deadlineTo,
    min_value: minValue,
    max_value: maxValue,
    open_only: openOnly ? "true" : "",
    sort,
    page,
    page_size: pageSize,
  });

  return requestJson(`/tenders/${query}`, { fallback: "Failed to load tenders" });
}

export async function getTender(id) {
  return requestJson(`/tenders/${id}`, { fallback: "Failed to load this tender" });
}

export async function createTender(values) {
  return requestJson("/tenders/", {
    method: "POST",
    body: JSON.stringify(pick(EMPTY_TENDER, values)),
    fallback: "Failed to save this tender",
  });
}

export async function updateTender(id, values) {
  return requestJson(`/tenders/${id}`, {
    method: "PUT",
    body: JSON.stringify(pick(EMPTY_TENDER, values)),
    fallback: "Failed to save this tender",
  });
}

export async function deleteTender(id) {
  await requestNoContent(`/tenders/${id}`, {
    method: "DELETE",
    fallback: "Failed to delete this tender",
  });
}

// --- nested records -----------------------------------------------------
//
// Each returns the updated tender, the way updateTender used to when it
// carried these arrays. Receipts and scans are stored through
// api/attachments.js before these are called; only the pointer travels here.

export const addCostItem = (tenderId, values) =>
  requestJson(`/tenders/${tenderId}/cost-items`, {
    method: "POST",
    body: JSON.stringify(pick(EMPTY_COST_ITEM, values)),
    fallback: "Failed to save this cost",
  });

export const updateCostItem = (tenderId, costItemId, values) =>
  requestJson(`/tenders/${tenderId}/cost-items/${costItemId}`, {
    method: "PUT",
    body: JSON.stringify(pick(EMPTY_COST_ITEM, values)),
    fallback: "Failed to save this cost",
  });

export const deleteCostItem = (tenderId, costItemId) =>
  requestJson(`/tenders/${tenderId}/cost-items/${costItemId}`, {
    method: "DELETE",
    fallback: "Failed to delete this cost",
  });

export const addCertificate = (tenderId, values) =>
  requestJson(`/tenders/${tenderId}/certificates`, {
    method: "POST",
    body: JSON.stringify(pick(EMPTY_TENDER_CERTIFICATE, values)),
    fallback: "Failed to save this certificate",
  });

export const updateCertificate = (tenderId, certificateId, values) =>
  requestJson(`/tenders/${tenderId}/certificates/${certificateId}`, {
    method: "PUT",
    body: JSON.stringify(pick(EMPTY_TENDER_CERTIFICATE, values)),
    fallback: "Failed to save this certificate",
  });

export const deleteCertificate = (tenderId, certificateId) =>
  requestJson(`/tenders/${tenderId}/certificates/${certificateId}`, {
    method: "DELETE",
    fallback: "Failed to delete this certificate",
  });

// --- attached documents -------------------------------------------------
//
// Dedicated endpoints, like a project's: the server appends or removes the
// link in one operation, so two people attaching documents at the same time
// cannot overwrite each other.

export async function linkDocument(tenderId, documentId) {
  return requestJson(`/tenders/${tenderId}/documents/${documentId}`, {
    method: "POST",
    fallback: "Failed to attach this document to the tender",
  });
}

export async function unlinkDocument(tenderId, documentId) {
  return requestJson(`/tenders/${tenderId}/documents/${documentId}`, {
    method: "DELETE",
    fallback: "Failed to detach this document from the tender",
  });
}

// --- filter vocabularies ------------------------------------------------

export async function listAuthorities() {
  return requestJson("/tenders/authorities", { fallback: "Failed to load authorities" });
}

export async function listTags() {
  return requestJson("/tenders/tags", { fallback: "Failed to load tags" });
}

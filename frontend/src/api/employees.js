/**
 * Employee, CV and certification data access.
 *
 * Backed by the /employees endpoints on the FastAPI backend, which persist to
 * MongoDB. Like api/projects.js, this module replaced a browser-local store
 * and kept the same function signatures and return shapes, so the screens
 * above it did not change when the endpoint arrived. CVs and certifications
 * are nested inside their employee (/employees/:id/cvs, ...), which is the
 * shape the store always presented.
 *
 * Only the metadata goes through here. Uploaded files go to the documents
 * endpoint through api/attachments.js, and each record keeps the document id.
 */

import { queryString, requestJson, requestNoContent } from "./http";
import { addDays, localToday } from "../lib/dates";

/** Field defaults, and the canonical field list for an employee record.
 *  Also the list of what the profile form may send — createEmployee and
 *  updateEmployee pick exactly these keys, so ids, timestamps, CVs and
 *  certifications can never be overwritten by a profile post. */
export const EMPTY_EMPLOYEE = {
  full_name: "",
  employee_code: "",
  designation: "",
  department: "",
  // The two dates experience is derived from. There is no experience_years
  // here any more: a typed number is stale the next day, and these two are
  // what the backend calculates Total Professional Experience and Experience
  // with VGIL from on every read.
  date_of_joining: "",
  career_start_date: "",
  highest_qualification: "",
  key_skills: "",
  email: "",
  phone: "",
  notes: "",
};

export const EMPTY_CV = {
  version_label: "",
  purpose: "",
  cv_date: "",
  uploaded_by: "",
  document_id: null,
  file_name: null,
};

export const EMPTY_CERTIFICATION = {
  name: "",
  issuing_body: "",
  certificate_number: "",
  issue_date: "",
  expiry_date: "",
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

/**
 * Newest CV first — the one that would be attached to a tender today.
 *
 * Client-side on purpose: it orders the CVs of one already-loaded employee
 * for display, which is not a query. The key is chosen once for the whole
 * list, not per comparison — a comparator that switches fields depending on
 * the pair it is handed does not define a consistent order. Undated versions
 * fall to the bottom. Both keys are ISO strings, so text order is date order.
 */
export function sortedCvs(employee) {
  const key = employee.cvs.some((cv) => cv.cv_date) ? "cv_date" : "created_at";
  return [...employee.cvs].sort((a, b) => {
    const left = a[key] ?? "";
    const right = b[key] ?? "";
    if (!left && !right) return 0;
    if (!left) return 1;
    if (!right) return -1;
    return left < right ? 1 : left > right ? -1 : 0;
  });
}

/** Certificates due to expire within this many days read as "Expiring soon"
 *  rather than "Valid" right up to the day they lapse. Matches the window
 *  the flattened certification index computes server-side
 *  (CERTIFICATION_EXPIRY_SOON_DAYS in routers/employees_mongo.py), so the two
 *  views can never disagree about what "soon" means. */
export const CERTIFICATION_EXPIRY_SOON_DAYS = 30;

/**
 * Whether a certificate has lapsed, is due to lapse soon, or is fine.
 * `today` is passed in so the caller decides what "now" means, and so this
 * stays testable. The flattened index gets its status from the server; this
 * covers the per-employee tab, which renders certifications straight off the
 * employee record.
 */
export function certificationStatus(certification, today = localToday()) {
  if (!certification.expiry_date) return "No expiry";
  if (certification.expiry_date < today) return "Expired";
  const soon = addDays(today, CERTIFICATION_EXPIRY_SOON_DAYS);
  return certification.expiry_date <= soon ? "Expiring soon" : "Valid";
}

export const CERTIFICATION_STATUSES = ["Valid", "Expiring soon", "Expired", "No expiry"];

// --- employees ----------------------------------------------------------

export async function listEmployees(filters = {}) {
  const {
    q = "",
    designation = "",
    department = "",
    certifications = [],
    minExperience = "",
    maxExperience = "",
    sort = "-updated_at",
    page = 1,
    pageSize = 10,
  } = filters;

  const query = queryString({
    q,
    designation,
    department,
    certifications,
    min_experience: minExperience,
    max_experience: maxExperience,
    sort,
    page,
    page_size: pageSize,
  });

  return requestJson(`/employees/${query}`, { fallback: "Failed to load employees" });
}

export async function getEmployee(id) {
  return requestJson(`/employees/${id}`, { fallback: "Failed to load this employee" });
}

export async function createEmployee(values) {
  return requestJson("/employees/", {
    method: "POST",
    body: JSON.stringify(pick(EMPTY_EMPLOYEE, values)),
    fallback: "Failed to save this employee",
  });
}

export async function updateEmployee(id, values) {
  return requestJson(`/employees/${id}`, {
    method: "PUT",
    body: JSON.stringify(pick(EMPTY_EMPLOYEE, values)),
    fallback: "Failed to save this employee",
  });
}

export async function deleteEmployee(id) {
  await requestNoContent(`/employees/${id}`, {
    method: "DELETE",
    fallback: "Failed to delete this employee",
  });
}

// --- nested records -----------------------------------------------------
//
// Each returns the updated employee, the way updateEmployee used to when it
// carried these arrays. The uploaded file is handled separately through
// api/attachments.js before these are called; only the pointer travels here.

export const addCv = (employeeId, values) =>
  requestJson(`/employees/${employeeId}/cvs`, {
    method: "POST",
    body: JSON.stringify(pick(EMPTY_CV, values)),
    fallback: "Failed to save this CV",
  });

export const updateCv = (employeeId, cvId, values) =>
  requestJson(`/employees/${employeeId}/cvs/${cvId}`, {
    method: "PUT",
    body: JSON.stringify(pick(EMPTY_CV, values)),
    fallback: "Failed to save this CV",
  });

export const deleteCv = (employeeId, cvId) =>
  requestJson(`/employees/${employeeId}/cvs/${cvId}`, {
    method: "DELETE",
    fallback: "Failed to delete this CV",
  });

export const addCertification = (employeeId, values) =>
  requestJson(`/employees/${employeeId}/certifications`, {
    method: "POST",
    body: JSON.stringify(pick(EMPTY_CERTIFICATION, values)),
    fallback: "Failed to save this certification",
  });

export const updateCertification = (employeeId, certificationId, values) =>
  requestJson(`/employees/${employeeId}/certifications/${certificationId}`, {
    method: "PUT",
    body: JSON.stringify(pick(EMPTY_CERTIFICATION, values)),
    fallback: "Failed to save this certification",
  });

export const deleteCertification = (employeeId, certificationId) =>
  requestJson(`/employees/${employeeId}/certifications/${certificationId}`, {
    method: "DELETE",
    fallback: "Failed to delete this certification",
  });

// --- the flat certification index ---------------------------------------

/**
 * Every certification held by anyone, flattened so "who holds a PMP?" is one
 * filter rather than a walk through every profile. The client sends `today`
 * itself — the same local calendar day certificationStatus defaults to — so
 * the index and the per-employee tab can never disagree about what Expired
 * means.
 */
export async function listCertifications(filters = {}) {
  const {
    q = "",
    name = "",
    issuingBody = "",
    status = "",
    sort = "name",
    page = 1,
    pageSize = 10,
    today = localToday(),
  } = filters;

  const query = queryString({
    q,
    name,
    issuing_body: issuingBody,
    status,
    today,
    sort,
    page,
    page_size: pageSize,
  });

  return requestJson(`/employees/certifications${query}`, {
    fallback: "Failed to load certifications",
  });
}

// --- filter vocabularies ------------------------------------------------

export async function listDesignations() {
  return requestJson("/employees/designations", { fallback: "Failed to load designations" });
}

export async function listDepartments() {
  return requestJson("/employees/departments", { fallback: "Failed to load departments" });
}

export async function listCertificationNames() {
  return requestJson("/employees/certification-names", {
    fallback: "Failed to load certificate names",
  });
}

export async function listIssuingBodies() {
  return requestJson("/employees/issuing-bodies", { fallback: "Failed to load issuing bodies" });
}

import { API_BASE_URL, apiFetch, errorMessage, queryString, requestJson } from "./http";

/** What each computed status is called on screen. "unknown" means no expiry
 *  date is recorded: that is not the same as "never expires". */
export const STATUS_LABELS = {
  valid: "Valid",
  expiring_soon: "Expiring soon",
  expired: "Expired",
  unknown: "Expiry not recorded",
};

/** One page of company documents: `{ items, total, page, page_size }`. */
export async function listCompanyDocuments(filters = {}) {
  const query = queryString({
    q: filters.q,
    category: filters.category,
    subcategory: filters.subcategory,
    document_type: filters.documentType,
    organisation: filters.organisation,
    status: filters.status,
    sort: filters.sort,
    page: filters.page,
    page_size: filters.pageSize,
  });
  return requestJson(`/company-documents/${query}`, { fallback: "Failed to load company documents" });
}

/** Values in use (with counts) for the filters and the form's suggestions. */
export async function getCompanyDocumentFacets() {
  return requestJson("/company-documents/facets", { fallback: "Failed to load filters" });
}

async function send(url, method, form, file, fallback) {
  const data = new FormData();
  Object.entries(form).forEach(([key, value]) => data.append(key, value ?? ""));
  if (file) data.append("document_file", file);
  const res = await apiFetch(`${API_BASE_URL}${url}`, { method, body: data });
  if (!res.ok) throw new Error(await errorMessage(res, fallback));
  return res.json();
}

export const createCompanyDocument = (form, file) =>
  send("/company-documents/", "POST", form, file, "Failed to save the company document");

export const updateCompanyDocument = (id, form, file) =>
  send(`/company-documents/${id}`, "PUT", form, file, "Failed to update the company document");

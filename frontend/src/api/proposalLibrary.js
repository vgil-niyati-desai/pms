import { API_BASE_URL, apiFetch, errorMessage, queryString, requestJson } from "./http";

/** One page of write-ups (without their long texts): `{ items, total, page, page_size }`. */
export async function listProposalItems(filters = {}) {
  const query = queryString({
    q: filters.q,
    section: filters.section,
    part: filters.part,
    file_type: filters.fileType,
    has_images: filters.hasImages === "" || filters.hasImages === undefined ? undefined : filters.hasImages,
    sort: filters.sort,
    page: filters.page,
    page_size: filters.pageSize,
  });
  return requestJson(`/proposal-library/${query}`, { fallback: "Failed to load write-ups" });
}

/** Every section in use with its count, in section order. */
export async function listProposalSections() {
  return requestJson("/proposal-library/sections", { fallback: "Failed to load sections" });
}

/** One write-up including the requirement and response text. */
export async function getProposalItem(id) {
  return requestJson(`/proposal-library/${id}`, { fallback: "Failed to load the write-up" });
}

/** The stored original as an HTML fragment: `{ html, fidelity }` ("formatted" for .docx, "text" for .rtf). */
export async function getProposalPreview(id) {
  return requestJson(`/proposal-library/${id}/preview`, { fallback: "Failed to load the document preview" });
}

export function proposalFileUrl(id) {
  return `${API_BASE_URL}/proposal-library/${id}/file`;
}

export async function createProposalItem(form, file) {
  const data = new FormData();
  Object.entries(form).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) data.append(key, value);
  });
  data.append("document_file", file);
  const res = await apiFetch(`${API_BASE_URL}/proposal-library/`, { method: "POST", body: data });
  if (!res.ok) throw new Error(await errorMessage(res, "Failed to add the write-up"));
  return res.json();
}

export async function updateProposalItem(id, body) {
  return requestJson(`/proposal-library/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
    fallback: "Failed to save the write-up",
  });
}

export async function deleteProposalItem(id) {
  const res = await apiFetch(`${API_BASE_URL}/proposal-library/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await errorMessage(res, "Failed to delete the write-up"));
}

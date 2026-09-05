/**
 * Projects data access.
 *
 * Backed by the /projects endpoints on the FastAPI backend, which persist to
 * MongoDB. This module replaced a browser-local store, and keeps the same
 * function signatures and return shapes it had, so the screens above it did
 * not change when the endpoint arrived.
 *
 * The difference that matters: searching, filtering, sorting and paging are
 * now done by the server. The screen receives one page and cannot reorder
 * what it was not sent, so every one of those is a query parameter rather
 * than something done here after the fact.
 */

import { queryString, requestJson, requestNoContent } from "./http";

export const PROJECT_STATUSES = ["Ongoing", "Completed"];

/**
 * Field defaults, and the canonical field list for a project record.
 *
 * Also the list of what a form may send: createProject and updateProject
 * pick exactly these keys, so ids, timestamps and document links can never
 * be overwritten by a form post.
 */
export const EMPTY_PROJECT = {
  title: "",
  client_name: "",
  reference_number: "",
  contract_value: "",
  start_date: "",
  end_date: "",
  status: "",
  department: "",
  scope_summary: "",
  notes: "",
  tags: [],
};

/** The editable fields, with unset ones sent as empty rather than missing. */
function editableFields(values) {
  const payload = {};
  for (const key of Object.keys(EMPTY_PROJECT)) {
    payload[key] = values[key] ?? EMPTY_PROJECT[key];
  }
  return payload;
}

/** The document types a project holds, resolved through the caller's lookup. */
export function documentTypesFor(project, documentTypeById) {
  const types = (project.document_ids || []).map((id) => documentTypeById[id]).filter(Boolean);
  return [...new Set(types)];
}

/**
 * One page of projects: `{ items, total, page, page_size }`.
 *
 * @param filters.has  document types the project must have, e.g. ["LOI"].
 *        The server joins projects to the document log itself to answer this,
 *        so unlike the browser store it needs no type lookup passed in — a
 *        `documentTypeById` from an existing caller is accepted and ignored.
 *        The list screen still uses that lookup for the evidence pills it
 *        draws per row, which is its own concern.
 */
export async function listProjects(filters = {}) {
  const {
    q = "",
    client = "",
    tags = [],
    tagMode = "any",
    status = "",
    has = [],
    sort = "-updated_at",
    page = 1,
    pageSize = 10,
  } = filters;

  const query = queryString({
    q,
    client,
    status,
    tags,
    tag_mode: tags.length > 1 ? tagMode : "any",
    has,
    sort,
    page,
    page_size: pageSize,
  });

  return requestJson(`/projects/${query}`, { fallback: "Failed to load projects" });
}

export async function getProject(id) {
  return requestJson(`/projects/${id}`, { fallback: "Failed to load this project" });
}

export async function createProject(values) {
  return requestJson("/projects/", {
    method: "POST",
    body: JSON.stringify(editableFields(values)),
    fallback: "Failed to save this project",
  });
}

export async function updateProject(id, values) {
  return requestJson(`/projects/${id}`, {
    method: "PUT",
    body: JSON.stringify(editableFields(values)),
    fallback: "Failed to save this project",
  });
}

export async function deleteProject(id) {
  await requestNoContent(`/projects/${id}`, {
    method: "DELETE",
    fallback: "Failed to delete this project",
  });
}

/**
 * Records which documents belong to a project.
 *
 * A dedicated endpoint rather than a field on updateProject: the server
 * appends to the list in one operation, so two people attaching documents at
 * the same time cannot overwrite each other's link.
 */
export async function linkDocument(projectId, documentId) {
  return requestJson(`/projects/${projectId}/documents/${documentId}`, {
    method: "POST",
    fallback: "Failed to attach this document to the project",
  });
}

export async function unlinkDocument(projectId, documentId) {
  return requestJson(`/projects/${projectId}/documents/${documentId}`, {
    method: "DELETE",
    fallback: "Failed to detach this document from the project",
  });
}

/** Distinct client names across all projects, for the list screen's filter. */
export async function listClients() {
  return requestJson("/projects/clients", { fallback: "Failed to load clients" });
}

/** The tag vocabulary, built from the tags already in use. */
export async function listTags() {
  return requestJson("/projects/tags", { fallback: "Failed to load tags" });
}

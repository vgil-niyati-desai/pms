import { useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  listDocuments,
  listDocumentTypes,
  listDocumentCategories,
  deleteDocument,
} from "../../api/documents";
import { listProjects } from "../../api/projects";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import ConfirmDelete from "../../components/ConfirmDelete";
import Field from "../../components/Field";
import FileViewLink from "../../components/FileViewLink";
import FilterBar from "../../components/FilterBar";
import FilterChips from "../../components/FilterChips";
import Pagination from "../../components/Pagination";
import SearchInput from "../../components/SearchInput";
import TagChip from "../../components/TagChip";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import usePageRecovery, { wholePage } from "../../hooks/usePageRecovery";
import { formatAmount } from "../../lib/format";
import { paths } from "../../app/routes";
import { documentCategoryOptions, documentTypeOptions } from "./documentTypes";

const dash = (value) => value || "—";

const PAGE_SIZE = 10;

// The project dropdown's own page size -- a vocabulary list, not a paged
// view, so it asks for as many as the server will give in one page rather
// than the 10 the list itself shows. Matches projects_mongo.py's own
// MAX_PAGE_SIZE, so this is "every project" for as long as there are no more
// of them than the server would let any single list ask for.
const PROJECT_OPTIONS_PAGE_SIZE = 200;

// In the URL like every other list, so a filtered page survives a refresh
// and can be linked to.
const FILTER_SCHEMA = { q: "", type: "", category: "", project: "", sort: "-created_at", page: 1 };

/** Where a document is held, from the owner ids it already carries. */
function owners(row) {
  return [
    row.project_id && { key: "project", label: "Project", to: paths.project(row.project_id) },
    row.employee_id && { key: "employee", label: "Employee", to: paths.employee(row.employee_id) },
    row.tender_id && { key: "tender", label: "Tender", to: paths.tender(row.tender_id) },
  ].filter(Boolean);
}

export default function DocumentList({ refreshKey, onAdd, onEdit, onDeleted, editingId }) {
  const { values, setValues } = useQueryParams(FILTER_SCHEMA);

  // useResource drops the answer to any request a newer one has replaced, so
  // a slow search can no longer land on top of the one typed after it.
  // refreshKey is in the list so a save from the drawer reloads the page.
  const page = wholePage(values.page);
  const load = useCallback(
    () =>
      listDocuments({
        q: values.q,
        document_type: values.type,
        category: values.category,
        project_id: values.project,
        sort: values.sort,
        page,
        pageSize: PAGE_SIZE,
      }),
    [values, page, refreshKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const documents = useResource(load, { initialData: { items: [], total: 0 } });
  // The page the URL names, as one that exists; see usePageRecovery.
  const movingPage = usePageRecovery({
    rawPage: values.page,
    page,
    resource: documents,
    pageSize: PAGE_SIZE,
    setPage: (page) => setValues({ page }),
  });
  const rows = documents.data.items;
  const total = documents.data.total;

  const loadTypes = useCallback(() => listDocumentTypes(), [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const types = useResource(loadTypes, { initialData: [] });
  const typeOptions = documentTypeOptions(types.data, values.type);

  // Keyed on refreshKey like the types, so a category typed in the drawer is
  // in the filter as soon as the entry carrying it is saved.
  const loadCategories = useCallback(() => listDocumentCategories(), [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const categories = useResource(loadCategories, { initialData: [] });
  const categoryOptions = documentCategoryOptions(categories.data, values.category);

  // The same /projects/ list the Projects screen reads, asked for every
  // project rather than one page of them -- a dropdown's vocabulary, not a
  // view of its own. Not reloaded on refreshKey: saving a document here
  // never creates or renames a project, unlike a type or category, which a
  // document's own form can introduce on the spot.
  const loadProjects = useCallback(() => listProjects({ sort: "title", pageSize: PROJECT_OPTIONS_PAGE_SIZE }), []);
  const projects = useResource(loadProjects, { initialData: { items: [], total: 0 } });
  const projectOptions = projects.data?.items ?? [];
  const selectedProjectTitle = projectOptions.find((p) => p.id === values.project)?.title;

  // The entry awaiting delete confirmation, or null when no dialog is open.
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  // Any change to what is shown starts again from the first page; staying on
  // page 4 of a list that now has one page shows nothing.
  function setFilter(patch) {
    setValues({ ...patch, page: 1 });
  }

  function clearFilters() {
    // The sort is how the list is read, not a filter, so it stays.
    setFilter({ q: "", type: "", category: "", project: "" });
  }

  function toggleSort(key) {
    setFilter({ sort: values.sort === key ? `-${key}` : key });
  }

  function sortHeader(key, label) {
    const descending = values.sort === `-${key}`;
    const active = descending || values.sort === key;
    return (
      <button
        type="button"
        className={active ? "sort-header sort-active" : "sort-header"}
        onClick={() => toggleSort(key)}
      >
        {label}
        <span aria-hidden="true">{active ? (descending ? " ▼" : " ▲") : ""}</span>
      </button>
    );
  }

  function requestDelete(row) {
    setDeleteError(null);
    setPendingDelete(row);
  }

  function cancelDelete() {
    setPendingDelete(null);
    setDeleteError(null);
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteDocument(pendingDelete.id);
      const deletedId = pendingDelete.id;
      setPendingDelete(null);
      if (onDeleted) onDeleted(deletedId);
      // The last row of a later page takes the page with it: step back one
      // rather than show an empty page. Anything else reloads in place.
      if (rows.length === 1 && page > 1) setValues({ page: page - 1 });
      else documents.reload();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this entry.");
    } finally {
      setDeleting(false);
    }
  }

  const chips = [
    values.q && { key: "q", label: `Search: ${values.q}`, onRemove: () => setFilter({ q: "" }) },
    values.type && {
      key: "type",
      label: `Type: ${values.type}`,
      onRemove: () => setFilter({ type: "" }),
    },
    values.category && {
      key: "category",
      label: `Category: ${values.category}`,
      onRemove: () => setFilter({ category: "" }),
    },
    // Falls back to the raw id on the rare render where the project list
    // has not resolved yet (a bookmarked or shared filtered link) rather
    // than waiting with nothing to show for a filter that is already active.
    values.project && {
      key: "project",
      label: `Project: ${selectedProjectTitle || values.project}`,
      onRemove: () => setFilter({ project: "" }),
    },
  ].filter(Boolean);
  const filtering = chips.length > 0;

  const columns = [
    {
      key: "document_type",
      header: sortHeader("document_type", "Type"),
      render: (r) => <StatusPill value={r.document_type} />,
    },
    { key: "category", header: "Category" },
    { key: "client_name", header: sortHeader("client_name", "Client") },
    {
      key: "project_title",
      header: sortHeader("project_title", "Project"),
      className: "cell-name",
      render: (r) => dash(r.project_title),
    },
    // The owning project's contract value, stamped onto the entry when it was
    // captured. The same figure the Projects screens show, so it is read the
    // same way here rather than as raw text.
    { key: "contract_value", header: "Value", render: (r) => formatAmount(r.contract_value) },
    {
      key: "document_date",
      header: sortHeader("document_date", "Date"),
      className: "cell-tight",
      render: (r) => dash(r.document_date),
    },
    { key: "submitted_by", header: "Added by" },
    {
      key: "owner",
      header: "Belongs to",
      className: "cell-tight",
      render: (r) => {
        const held = owners(r);
        if (held.length === 0) return dash(null);
        return held.map((owner, index) => (
          <span key={owner.key}>
            {index > 0 && ", "}
            <Link to={owner.to}>{owner.label}</Link>
          </span>
        ));
      },
    },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      // The chip marks an entry that was created by redacting another one.
      // It belongs beside the file rather than beside the type, because what
      // is redacted is the file -- the type is the same as the original's.
      render: (r) => (
        <>
          <FileViewLink
            documentId={r.file_name ? r.id : null}
            fileName={r.file_name}
            title={`${r.document_type} — ${r.client_name}`}
          />
          {r.redacted_from_id && <TagChip value="Redacted copy" />}
        </>
      ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      // Edit was dropped when the row itself became the way in, the way every
      // other table in the app opens a record. Delete stays: it is not what
      // clicking a row should do, and DataTable ignores clicks on a control.
      render: (r) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm btn-danger" onClick={() => requestDelete(r)}>
            Delete
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="list-layout">
      {/* Both text fields report their value once typing pauses, so a search
          is one request per pause, not one per keystroke. */}
      <FilterBar
        showClear={filtering}
        onClear={clearFilters}
        search={
          <SearchInput
            id="document-search"
            value={values.q}
            onChange={(q) => setFilter({ q })}
            placeholder="Type, category, client, project, reference no., notes"
          />
        }
      >
        <Field label="Document type" htmlFor="filter-document-type">
          <select
            id="filter-document-type"
            value={values.type}
            onChange={(e) => setFilter({ type: e.target.value })}
          >
            <option value="">All types</option>
            {typeOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>

        {/* Picked from what the log already holds, not typed. The filter
            matches a category exactly, so typing one was a filter that found
            nothing whenever the spelling differed by a capital or an
            ampersand — and there was no way to tell from the empty result
            which had happened. */}
        <Field label="Category" htmlFor="filter-category">
          <select
            id="filter-category"
            value={values.category}
            onChange={(e) => setFilter({ category: e.target.value })}
          >
            <option value="">All categories</option>
            {categoryOptions.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Project" htmlFor="filter-project">
          <select
            id="filter-project"
            value={values.project}
            onChange={(e) => setFilter({ project: e.target.value })}
          >
            <option value="">All projects</option>
            {projectOptions.map((project) => (
              <option key={project.id} value={project.id}>
                {project.title}
              </option>
            ))}
          </select>
        </Field>
      </FilterBar>

      <section className="card list-panel">
        <div className="list-head">
          <h2>Documents {!documents.loading && !documents.error && `(${total})`}</h2>
          {/* The add is a drawer, not a route, so it has no topbar button of
              its own the way the other three areas do — it sits here, as it
              does on every other drawer-backed list in the app. */}
          <button type="button" className="btn btn-primary" onClick={onAdd}>
            + Add document
          </button>
        </div>

        <FilterChips chips={chips} />

        <DataTable
          columns={columns}
          rows={rows}
          loading={documents.loading || movingPage}
          error={documents.error}
          onRetry={documents.reload}
          rowClassName={(r) => (r.id === editingId ? "row-editing" : undefined)}
          onRowClick={onEdit}
          empty={
            filtering ? (
              <EmptyState
                message="No documents match your filters."
                action={
                  <button type="button" className="btn btn-sm" onClick={clearFilters}>
                    Clear all filters
                  </button>
                }
              />
            ) : (
              <EmptyState
                message="No documents yet."
                action={
                  <button type="button" className="btn btn-sm" onClick={onAdd}>
                    Add the first document
                  </button>
                }
              />
            )
          }
        />

        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(page) => setValues({ page })}
        />
      </section>

      {pendingDelete && (
        <ConfirmDelete
          title="Delete this entry?"
          busy={deleting}
          error={deleteError}
          onConfirm={confirmDelete}
          onCancel={cancelDelete}
        >
          <p className="muted">
            {pendingDelete.document_type} &mdash; {pendingDelete.client_name}
            {pendingDelete.project_title ? ` (${pendingDelete.project_title})` : ""}
          </p>
          <p className="muted">
            {pendingDelete.file_name
              ? `The record and its uploaded file (${pendingDelete.file_name}) will be permanently removed.`
              : "The record will be permanently removed."}{" "}
            This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </div>
  );
}

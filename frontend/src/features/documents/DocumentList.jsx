import { useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { listDocuments, listDocumentTypes, deleteDocument } from "../../api/documents";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import ConfirmDelete from "../../components/ConfirmDelete";
import Field from "../../components/Field";
import FileViewLink from "../../components/FileViewLink";
import Pagination from "../../components/Pagination";
import SearchInput from "../../components/SearchInput";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { paths } from "../../app/routes";
import { documentTypeOptions } from "./documentTypes";

const dash = (value) => value || "—";

const PAGE_SIZE = 25;

// In the URL like every other list, so a filtered page survives a refresh
// and can be linked to.
const FILTER_SCHEMA = { q: "", type: "", category: "", sort: "-created_at", page: 1 };

/** Where a document is held, from the owner ids it already carries. */
function owners(row) {
  return [
    row.project_id && { key: "project", label: "Project", to: paths.project(row.project_id) },
    row.employee_id && { key: "employee", label: "Employee", to: paths.employee(row.employee_id) },
    row.tender_id && { key: "tender", label: "Tender", to: paths.tender(row.tender_id) },
  ].filter(Boolean);
}

export default function DocumentList({ refreshKey, onEdit, onDeleted, editingId }) {
  const { values, setValues } = useQueryParams(FILTER_SCHEMA);

  // useResource drops the answer to any request a newer one has replaced, so
  // a slow search can no longer land on top of the one typed after it.
  // refreshKey is in the list so a save from the drawer reloads the page.
  const load = useCallback(
    () =>
      listDocuments({
        q: values.q,
        document_type: values.type,
        category: values.category,
        sort: values.sort,
        page: values.page,
        pageSize: PAGE_SIZE,
      }),
    [values, refreshKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const documents = useResource(load, { initialData: { items: [], total: 0 } });
  const rows = documents.data.items;
  const total = documents.data.total;

  const loadTypes = useCallback(() => listDocumentTypes(), [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const types = useResource(loadTypes, { initialData: [] });
  const typeOptions = documentTypeOptions(types.data, values.type);

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
    setFilter({ q: "", type: "", category: "" });
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
      if (rows.length === 1 && values.page > 1) setValues({ page: values.page - 1 });
      else documents.reload();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this entry.");
    } finally {
      setDeleting(false);
    }
  }

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
    { key: "contract_value", header: "Value", render: (r) => dash(r.contract_value) },
    {
      key: "document_date",
      header: sortHeader("document_date", "Date"),
      className: "cell-tight",
      render: (r) => dash(r.document_date),
    },
    { key: "submitted_by", header: "Submitted by" },
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
      render: (r) => (
        <FileViewLink
          documentId={r.file_name ? r.id : null}
          fileName={r.file_name}
          title={`${r.document_type} — ${r.client_name}`}
        />
      ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (r) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm" onClick={() => onEdit && onEdit(r)}>
            Edit
          </button>
          <button type="button" className="btn btn-sm btn-danger" onClick={() => requestDelete(r)}>
            Delete
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="card">
      <h2>All Entries {!documents.loading && `(${total})`}</h2>

      {/* Both text fields report their value once typing pauses, so a search
          is one request per pause, not one per keystroke. */}
      <div className="filters">
        <SearchInput
          id="search"
          value={values.q}
          onChange={(q) => setFilter({ q })}
          placeholder="Client, project, reference no."
        />
        <Field label="Document type" htmlFor="filter_type">
          <select id="filter_type" value={values.type} onChange={(e) => setFilter({ type: e.target.value })}>
            <option value="">All types</option>
            {typeOptions.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </Field>
        <SearchInput
          id="filter_category"
          label="Category"
          value={values.category}
          onChange={(category) => setFilter({ category })}
          placeholder="Exact category text"
        />
        {(values.q || values.type || values.category) && (
          <div className="field filter-buttons">
            <button type="button" className="btn" onClick={clearFilters}>Clear</button>
          </div>
        )}
      </div>

      {documents.error && <div className="alert alert-error">{documents.error}</div>}

      <DataTable
        columns={columns}
        rows={rows}
        loading={documents.loading}
        rowClassName={(r) => (r.id === editingId ? "row-editing" : undefined)}
        empty={<EmptyState message="No entries match your filters." />}
      />

      <Pagination
        page={values.page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={(page) => setValues({ page })}
      />

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

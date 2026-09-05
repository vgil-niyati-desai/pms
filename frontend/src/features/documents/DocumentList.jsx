import { useEffect, useState, useCallback } from "react";
import { listDocuments, deleteDocument } from "../../api/documents";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import ConfirmDelete from "../../components/ConfirmDelete";
import Field from "../../components/Field";
import FileViewLink from "../../components/FileViewLink";

const DOCUMENT_TYPES = ["LOI", "Work Order", "Completion Certificate", "Purchase Order", "Contract"];

const dash = (value) => value || "—";

export default function DocumentList({ refreshKey, onEdit, onDeleted, editingId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [searchInput, setSearchInput] = useState("");
  const [documentType, setDocumentType] = useState("");
  const [category, setCategory] = useState("");

  // The entry awaiting delete confirmation, or null when no dialog is open.
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDocuments({
        q: searchInput || undefined,
        document_type: documentType || undefined,
        category: category || undefined,
      });
      setRows(data);
    } catch (err) {
      setError(err.message || "Could not load entries.");
    } finally {
      setLoading(false);
    }
  }, [searchInput, documentType, category]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  function handleSearchSubmit(e) {
    e.preventDefault();
    load();
  }

  function clearFilters() {
    setSearchInput("");
    setDocumentType("");
    setCategory("");
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
      await load();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this entry.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = [
    { key: "document_type", header: "Type", render: (r) => <StatusPill value={r.document_type} /> },
    { key: "category", header: "Category" },
    { key: "client_name", header: "Client" },
    {
      key: "project_title",
      header: "Project",
      className: "cell-name",
      render: (r) => dash(r.project_title),
    },
    { key: "contract_value", header: "Value", render: (r) => dash(r.contract_value) },
    {
      key: "document_date",
      header: "Date",
      className: "cell-tight",
      render: (r) => dash(r.document_date),
    },
    { key: "submitted_by", header: "Submitted by" },
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
      <h2>All Entries {!loading && `(${rows.length})`}</h2>

      <form className="filters" onSubmit={handleSearchSubmit}>
        <Field label="Search" htmlFor="search">
          <input
            id="search"
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Client, project, reference no."
          />
        </Field>
        <Field label="Document type" htmlFor="filter_type">
          <select id="filter_type" value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
            <option value="">All types</option>
            {DOCUMENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </Field>
        <Field label="Category" htmlFor="filter_category">
          <input
            id="filter_category"
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="Exact category text"
          />
        </Field>
        <div className="field filter-buttons">
          <button type="submit" className="btn">Filter</button>
          {(searchInput || documentType || category) && (
            <button type="button" className="btn" onClick={clearFilters}>Clear</button>
          )}
        </div>
      </form>

      {error && <div className="alert alert-error">{error}</div>}

      <DataTable
        columns={columns}
        rows={rows}
        loading={loading}
        rowClassName={(r) => (r.id === editingId ? "row-editing" : undefined)}
        empty={<EmptyState message="No entries match your filters." />}
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

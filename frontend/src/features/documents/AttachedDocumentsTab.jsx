import { useState } from "react";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import Field from "../../components/Field";
import FileViewLink from "../../components/FileViewLink";
import { dash } from "../../lib/format";

/**
 * Groups documents by type, leading with the types the owning area cares about
 * most, then anything else alphabetically.
 */
function groupByType(documents, typeOrder) {
  const leading = typeOrder.filter((type) =>
    documents.some((document) => document.document_type === type),
  );
  const others = [
    ...new Set(
      documents.map((d) => d.document_type).filter((type) => !typeOrder.includes(type)),
    ),
  ].sort();
  return [...leading, ...others].map((type) => ({
    type,
    rows: documents.filter((document) => document.document_type === type),
  }));
}

/** The documents attached to one record — a project or a tender. */
export default function AttachedDocumentsTab({
  documents,
  typeOrder,
  loading,
  error,
  onOpen,
  onAdd,
  emptyMessage = "No documents attached yet.",
}) {
  const [typeFilter, setTypeFilter] = useState("");

  const filtered = typeFilter
    ? documents.filter((document) => document.document_type === typeFilter)
    : documents;
  const groups = groupByType(filtered, typeOrder);
  // Taken from the same grouping so the dropdown lists types in the order the
  // sections below appear in, rather than the order the documents happen to
  // have been added.
  const presentTypes = groupByType(documents, typeOrder).map((group) => group.type);

  const columns = [
    {
      key: "reference_number",
      header: "Reference no.",
      className: "cell-name",
      render: (d) => dash(d.reference_number),
    },
    {
      key: "document_date",
      header: "Date",
      className: "cell-tight",
      render: (d) => dash(d.document_date),
    },
    { key: "category", header: "Category", render: (d) => dash(d.category) },
    { key: "submitted_by", header: "Added by" },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      render: (document) => (
        <FileViewLink
          documentId={document.file_name ? document.id : null}
          fileName={document.file_name}
          title={document.document_type}
        />
      ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (document) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm" onClick={() => onOpen(document)}>
            Open
          </button>
        </div>
      ),
    },
  ];

  return (
    <section className="card">
      <div className="list-head">
        <h3>Documents {!loading && `(${documents.length})`}</h3>
        <button type="button" className="btn btn-primary" onClick={() => onAdd()}>
          + Add document
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {presentTypes.length > 1 && (
        <div className="filters">
          <Field label="Document type" htmlFor="tab-type-filter">
            <select
              id="tab-type-filter"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All types</option>
              {presentTypes.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}

      {loading ? (
        <p className="muted">Loading...</p>
      ) : groups.length === 0 ? (
        <EmptyState
          message={typeFilter ? "No documents of that type on this record." : emptyMessage}
          action={
            typeFilter ? (
              <button type="button" className="btn btn-sm" onClick={() => setTypeFilter("")}>
                Show all types
              </button>
            ) : (
              <button type="button" className="btn btn-sm" onClick={() => onAdd()}>
                Add the first document
              </button>
            )
          }
        />
      ) : (
        groups.map((group) => (
          <div key={group.type} className="doc-group">
            <h4>
              {group.type} <span className="muted">({group.rows.length})</span>
            </h4>
            <DataTable columns={columns} rows={group.rows} empty={null} />
          </div>
        ))
      )}
    </section>
  );
}

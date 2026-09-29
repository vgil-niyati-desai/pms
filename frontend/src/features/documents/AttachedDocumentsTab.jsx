import { useState } from "react";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import Field from "../../components/Field";
import FileViewLink from "../../components/FileViewLink";
import StatusPill from "../../components/StatusPill";
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
  typeFilter: controlledTypeFilter,
  onTypeFilterChange,
  emptyMessage = "No documents attached yet.",
}) {
  // The filter is this tab's own state unless an owner drives it. The
  // project screen does, so that clicking a held evidence pill can land
  // here already narrowed to that type and the query string can carry it;
  // anything passing neither prop keeps the local state it always had.
  const [ownTypeFilter, setOwnTypeFilter] = useState("");
  const controlled = controlledTypeFilter !== undefined;
  const typeFilter = controlled ? controlledTypeFilter : ownTypeFilter;
  const setTypeFilter = controlled ? onTypeFilterChange : setOwnTypeFilter;

  // Only when there is nothing on screen yet. A reload after a save keeps
  // the rows in place, the way the page around it has since the loading
  // check was put on the record itself.
  const initialLoading = loading && documents.length === 0;

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
      // Both halves of what identifies a document at a glance, stacked in
      // the column that leads the row: the reference it is filed under and
      // the file it actually is. The file name used to be reachable only by
      // opening the preview.
      header: "Reference & file",
      className: "cell-name",
      render: (d) => (
        <div className="doc-cell">
          <span className="doc-cell-ref">{dash(d.reference_number)}</span>
          {d.file_name && <span className="doc-cell-file">{d.file_name}</span>}
        </div>
      ),
    },
    {
      key: "document_date",
      header: "Date",
      className: "cell-tight",
      render: (d) => dash(d.document_date),
    },
    { key: "category", header: "Category", render: (d) => dash(d.category) },
    // dash(), like every other optional column: without it a record with no
    // submitted_by renders an empty cell rather than the "—" the rest of the
    // row uses for the same thing.
    { key: "submitted_by", header: "Added by", render: (d) => dash(d.submitted_by) },
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
        <h3>Documents {!initialLoading && `(${documents.length})`}</h3>
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

      {initialLoading ? (
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
            {/* The type carries the colour it already has everywhere else in
                the app, so a group is recognised by the same pill the
                document log and the evidence strip use. */}
            <h4 className="doc-group-head">
              <StatusPill value={group.type} />
              <span className="doc-group-count">
                {group.rows.length} {group.rows.length === 1 ? "document" : "documents"}
              </span>
            </h4>
            {/* Opening a row opens the record, the way every other table on
                the app does it. The Open button stays for the affordance, and
                DataTable ignores clicks that land on a control. */}
            <DataTable columns={columns} rows={group.rows} empty={null} onRowClick={onOpen} />
          </div>
        ))
      )}
    </section>
  );
}

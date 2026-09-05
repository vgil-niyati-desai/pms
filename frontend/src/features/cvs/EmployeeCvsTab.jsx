import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import FileViewLink from "../../components/FileViewLink";
import { sortedCvs } from "../../api/employees";
import { dash } from "../../lib/format";
import { cvLabel } from "./employeeFields";

/**
 * An employee's CV versions, newest first. Which one is current matters more
 * than any other fact here, so the top row is badged rather than left to be
 * inferred from the dates.
 */
export default function EmployeeCvsTab({ employee, onOpen, onAdd }) {
  const rows = sortedCvs(employee);

  const columns = [
    {
      key: "version_label",
      header: "Version",
      className: "cell-name",
      render: (cv, index) => (
        <>
          {cvLabel(cv)}
          {index === 0 && <span className="badge-latest">Latest</span>}
        </>
      ),
    },
    { key: "purpose", header: "Purpose", render: (cv) => dash(cv.purpose) },
    { key: "cv_date", header: "Date", className: "cell-tight", render: (cv) => dash(cv.cv_date) },
    { key: "uploaded_by", header: "Uploaded by", render: (cv) => dash(cv.uploaded_by) },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      render: (cv) => (
        <FileViewLink documentId={cv.document_id} fileName={cv.file_name} title={cvLabel(cv)} />
      ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (cv) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm" onClick={() => onOpen(cv)}>
            Open
          </button>
        </div>
      ),
    },
  ];

  // The Latest badge depends on position, which DataTable's render does not
  // pass, so the index is baked into each row before rendering.
  const indexed = rows.map((cv, index) => ({ ...cv, _index: index }));
  const columnsWithIndex = columns.map((column) =>
    column.key === "version_label"
      ? { ...column, render: (cv) => column.render(cv, cv._index) }
      : column,
  );

  return (
    <section className="card">
      <div className="list-head">
        <h3>CVs ({rows.length})</h3>
        <button type="button" className="btn btn-primary" onClick={() => onAdd()}>
          + Upload CV
        </button>
      </div>

      <DataTable
        columns={columnsWithIndex}
        rows={indexed}
        empty={
          <EmptyState
            message="No CV uploaded for this employee yet."
            action={
              <button type="button" className="btn btn-sm" onClick={() => onAdd()}>
                Upload the first CV
              </button>
            }
          />
        }
      />
    </section>
  );
}

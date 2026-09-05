import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import FileViewLink from "../../components/FileViewLink";
import { certificationStatus } from "../../api/employees";
import { dash } from "../../lib/format";

export default function EmployeeCertificationsTab({ employee, onOpen, onAdd }) {
  const columns = [
    { key: "name", header: "Certificate", className: "cell-name" },
    { key: "issuing_body", header: "Issued by", render: (c) => dash(c.issuing_body) },
    { key: "certificate_number", header: "Certificate no.", render: (c) => dash(c.certificate_number) },
    { key: "issue_date", header: "Issued", className: "cell-tight", render: (c) => dash(c.issue_date) },
    {
      key: "expiry_date",
      header: "Expires",
      className: "cell-tight",
      render: (certification) => (
        <>
          {dash(certification.expiry_date)} <StatusPill value={certificationStatus(certification)} />
        </>
      ),
    },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      render: (certification) => (
        <FileViewLink
          documentId={certification.document_id}
          fileName={certification.file_name}
          title={certification.name}
        />
      ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (certification) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm" onClick={() => onOpen(certification)}>
            Open
          </button>
        </div>
      ),
    },
  ];

  return (
    <section className="card">
      <div className="list-head">
        <h3>Certifications ({employee.certifications.length})</h3>
        <button type="button" className="btn btn-primary" onClick={() => onAdd()}>
          + Add certification
        </button>
      </div>

      <DataTable
        columns={columns}
        rows={employee.certifications}
        empty={
          <EmptyState
            message="No certifications recorded for this employee yet."
            action={
              <button type="button" className="btn btn-sm" onClick={() => onAdd()}>
                Add the first certification
              </button>
            }
          />
        }
      />
    </section>
  );
}

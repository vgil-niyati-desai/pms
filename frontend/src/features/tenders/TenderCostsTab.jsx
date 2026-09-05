import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import FileViewLink from "../../components/FileViewLink";
import { dash, formatAmount, sumAmounts } from "../../lib/format";
import { certificateValidity } from "./tenderFields";

const fileCell = (record) => (
  <FileViewLink
    documentId={record.document_id}
    fileName={record.file_name}
    title={record.name || record.cost_type}
  />
);

/**
 * What this tender cost to enter, and the certificates bought for it.
 *
 * Two separate records that answer the same question — what had to be paid for
 * or obtained before submitting — so they share one tab, with the money
 * totalled at the point it is read.
 */
export default function TenderCostsTab({
  tender,
  onOpenCost,
  onAddCost,
  onOpenCertificate,
  onAddCertificate,
}) {
  const total = sumAmounts(tender.cost_items.map((item) => item.amount));

  const costColumns = [
    { key: "cost_type", header: "Type", className: "cell-name" },
    {
      key: "amount",
      header: "Amount",
      className: "cell-amount",
      render: (item) => formatAmount(item.amount),
    },
    { key: "payment_mode", header: "Paid by", render: (item) => dash(item.payment_mode) },
    {
      key: "instrument_number",
      header: "Instrument no.",
      render: (item) => dash(item.instrument_number),
    },
    { key: "paid_on", header: "Date", className: "cell-tight", render: (item) => dash(item.paid_on) },
    { key: "file", header: "Receipt", className: "cell-tight", render: fileCell },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (item) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm" onClick={() => onOpenCost(item)}>
            Open
          </button>
        </div>
      ),
    },
  ];

  const certificateColumns = [
    { key: "name", header: "Certificate", className: "cell-name" },
    { key: "issuing_body", header: "Issued by", render: (c) => dash(c.issuing_body) },
    {
      key: "certificate_number",
      header: "Certificate no.",
      render: (c) => dash(c.certificate_number),
    },
    { key: "valid_from", header: "Valid from", className: "cell-tight", render: (c) => dash(c.valid_from) },
    {
      key: "valid_to",
      header: "Valid to",
      className: "cell-tight",
      render: (certificate) => (
        <>
          {dash(certificate.valid_to)} <StatusPill value={certificateValidity(certificate)} />
        </>
      ),
    },
    { key: "file", header: "File", className: "cell-tight", render: fileCell },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (certificate) => (
        <div className="row-actions">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onOpenCertificate(certificate)}
          >
            Open
          </button>
        </div>
      ),
    },
  ];

  return (
    <>
      <section className="card">
        <div className="list-head">
          <h3>Costs ({tender.cost_items.length})</h3>
          <button type="button" className="btn btn-primary" onClick={() => onAddCost()}>
            + Add cost
          </button>
        </div>

        <DataTable
          columns={costColumns}
          rows={tender.cost_items}
          empty={
            <EmptyState
              message="No costs recorded for this tender yet."
              action={
                <button type="button" className="btn btn-sm" onClick={() => onAddCost()}>
                  Add the first cost
                </button>
              }
            />
          }
        />

        {tender.cost_items.length > 0 && (
          <p className="cost-total">
            Total recorded <strong>{formatAmount(total)}</strong>
          </p>
        )}
      </section>

      <section className="card">
        <div className="list-head">
          <h3>Certificates ({tender.certificates.length})</h3>
          <button type="button" className="btn btn-primary" onClick={() => onAddCertificate()}>
            + Add certificate
          </button>
        </div>

        <DataTable
          columns={certificateColumns}
          rows={tender.certificates}
          empty={
            <EmptyState
              message="No certificates obtained for this tender yet."
              action={
                <button type="button" className="btn btn-sm" onClick={() => onAddCertificate()}>
                  Add the first certificate
                </button>
              }
            />
          }
        />
      </section>
    </>
  );
}

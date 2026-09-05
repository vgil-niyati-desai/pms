import { sumAmounts, dash, formatAmount, formatTimestamp } from "../../lib/format";

/**
 * The tender's own fields, led by a readiness summary.
 *
 * Qualification criteria and attached evidence belong in this summary too, but
 * they arrive in Phase 4 — what is counted here is only what exists.
 */
export default function TenderOverviewTab({ tender, documentCount }) {
  const recordedCost = sumAmounts(tender.cost_items.map((item) => item.amount));

  const fields = [
    ["Issuing authority", tender.issuing_authority],
    ["Reference number", dash(tender.reference_number)],
    ["Tender type", dash(tender.tender_type)],
    ["Estimated value", formatAmount(tender.estimated_value)],
    ["EMD amount", formatAmount(tender.emd_amount)],
    ["Tender fee", formatAmount(tender.tender_fee)],
    ["Published", dash(tender.published_date)],
    ["Submission deadline", dash(tender.submission_deadline)],
    ["Submission mode", dash(tender.submission_mode)],
    ["Created", formatTimestamp(tender.created_at)],
    ["Last updated", formatTimestamp(tender.updated_at)],
  ];

  const summary = [
    ["Cost recorded", formatAmount(recordedCost)],
    ["Cost items", tender.cost_items.length],
    ["Certificates", tender.certificates.length],
    ["Documents", documentCount],
  ];

  return (
    <>
      <section className="card">
        <h3>Readiness</h3>
        <dl className="detail-facts">
          {summary.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="card">
        <h3>Details</h3>
        <dl className="detail-facts detail-facts-wide">
          {fields.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {tender.notes && (
        <section className="card">
          <h3>Notes</h3>
          <p className="prose">{tender.notes}</p>
        </section>
      )}
    </>
  );
}

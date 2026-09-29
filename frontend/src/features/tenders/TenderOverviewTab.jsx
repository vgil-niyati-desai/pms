import { sumAmounts, dash, formatAmount, formatTimestamp } from "../../lib/format";
import { criteriaCountLabel } from "./criteriaFields";

/**
 * The tender's own fields, led by a readiness summary: what the tender asks
 * for, then what has been gathered for it. What is counted here is only what
 * exists -- whether the criteria are met is not worked out yet.
 */
export default function TenderOverviewTab({ tender, documentCount, citedProjectCount }) {
  const recordedCost = sumAmounts(tender.cost_items.map((item) => item.amount));

  const fields = [
    ["Issuing authority", tender.issuing_authority],
    ["Reference number", dash(tender.reference_number)],
    ["Tender type", dash(tender.tender_type)],
    ["Estimated value", formatAmount(tender.estimated_value)],
    ["EMD required", formatAmount(tender.emd_amount)],
    ["Tender fee required", formatAmount(tender.tender_fee)],
    ["Published", dash(tender.published_date)],
    ["Submission deadline", dash(tender.submission_deadline)],
    ["Submission mode", dash(tender.submission_mode)],
    ["Created", formatTimestamp(tender.created_at)],
    ["Last updated", formatTimestamp(tender.updated_at)],
  ];

  const summary = [
    ["Criteria", criteriaCountLabel(tender.criteria)],
    ["Cost recorded", formatAmount(recordedCost)],
    ["Cost items", tender.cost_items.length],
    ["Certificates", tender.certificates.length],
    ["Documents", documentCount],
    // The past work this bid puts forward. A count of citations, not of
    // anything the tender owns.
    ["Cited projects", citedProjectCount],
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

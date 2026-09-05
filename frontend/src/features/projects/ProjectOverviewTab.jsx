import EvidenceStrip from "./EvidenceStrip";
import { EVIDENCE_TYPES, dash, formatPeriod, formatTimestamp } from "./projectFields";

/**
 * The project's own fields, led by whether it can be cited as evidence yet.
 * Missing evidence is a button, because the next thing you want after seeing
 * a gap is to fill it.
 */
export default function ProjectOverviewTab({ project, heldTypes, onAddMissing }) {
  const missing = EVIDENCE_TYPES.filter((type) => !heldTypes.includes(type));

  const fields = [
    ["Client / organization", project.client_name],
    ["Reference number", dash(project.reference_number)],
    ["Contract value", dash(project.contract_value)],
    ["Period", formatPeriod(project.start_date, project.end_date)],
    ["Status", dash(project.status)],
    ["Department / domain", dash(project.department)],
    ["Created", formatTimestamp(project.created_at)],
    ["Last updated", formatTimestamp(project.updated_at)],
  ];

  return (
    <>
      <section className="card">
        <h3>Evidence</h3>
        <p className="muted hint">
          {missing.length === 0
            ? `All ${EVIDENCE_TYPES.length} evidence documents are attached — this project is ready to cite in a tender.`
            : `Missing ${missing.length} of ${EVIDENCE_TYPES.length}. Select one to add it.`}
        </p>
        <EvidenceStrip heldTypes={heldTypes} onMissingClick={onAddMissing} />
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

      {(project.scope_summary || project.notes) && (
        <section className="card">
          {project.scope_summary && (
            <>
              <h3>Scope summary</h3>
              <p className="prose">{project.scope_summary}</p>
            </>
          )}
          {project.notes && (
            <>
              <h3>Notes</h3>
              <p className="prose">{project.notes}</p>
            </>
          )}
        </section>
      )}
    </>
  );
}

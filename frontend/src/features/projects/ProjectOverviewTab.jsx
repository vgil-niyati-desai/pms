import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import { TableSkeleton } from "../../components/Skeleton";
import EvidenceStrip from "./EvidenceStrip";
import { EVIDENCE_TYPES, dash, formatTimestamp } from "./projectFields";

// One row per tender citing this project. Status carries the pill it wears
// everywhere else, so how a bid went reads at a glance -- which is most of
// why anyone looks at this list.
const citedByColumns = [
  { key: "title", header: "Tender", className: "cell-name" },
  {
    key: "issuing_authority",
    header: "Authority",
    render: (tender) => dash(tender.issuing_authority),
  },
  {
    key: "reference_number",
    header: "Reference no.",
    render: (tender) => dash(tender.reference_number),
  },
  {
    key: "submission_deadline",
    header: "Deadline",
    className: "cell-tight",
    render: (tender) => dash(tender.submission_deadline),
  },
  {
    key: "status",
    header: "Status",
    className: "cell-tight",
    render: (tender) => <StatusPill value={tender.status} />,
  },
];

/**
 * The project's own fields, led by whether it can be cited as evidence yet.
 * Missing evidence is a button, because the next thing you want after seeing
 * a gap is to fill it.
 */
export default function ProjectOverviewTab({
  project,
  heldTypes,
  documentsLoading,
  documentsError,
  onAddMissing,
  onShowDocuments,
  citingTenders = [],
  citingTendersLoading = false,
  citingTendersError = null,
  onOpenTender,
}) {
  const missing = EVIDENCE_TYPES.filter((type) => !heldTypes.includes(type));
  const attached = EVIDENCE_TYPES.length - missing.length;

  // heldTypes is empty both when nothing is attached and when the document
  // log has not been read yet, so the count is only true once it has been.
  // Saying "Missing 5 of 5" while the documents are still loading -- or when
  // they failed to load altogether -- reads as a finding about the project.
  const evidenceHint = documentsLoading
    ? "Checking which documents are attached..."
    : documentsError
      ? "Attached documents could not be loaded, so the marks below are not reliable."
      : missing.length === 0
        ? `All ${EVIDENCE_TYPES.length} evidence documents are attached — this project is ready to cite in a tender.`
        : `Missing ${missing.length} of ${EVIDENCE_TYPES.length}. Select one to add it.`;

  // Client, reference, value, duration, department and status are all in
  // the header a few pixels above this tab. Repeating them here filled the
  // Overview with a second copy of the identity block and pushed the two
  // facts that are NOT up there -- when this record was made and last
  // touched -- to the bottom of a list nobody read to the end of.
  const record = [
    ["Created", formatTimestamp(project.created_at)],
    ["Last updated", formatTimestamp(project.updated_at)],
  ];

  return (
    <>
      <section className="card evidence-card">
        {/* The same head the Documents tab uses: the heading on the left, the
            figure it is asking about on the right, so the state is legible
            without reading the sentence under it. */}
        <div className="list-head">
          <h3>Evidence</h3>
          {!documentsLoading && !documentsError && (
            <span className="muted evidence-count">
              {attached} of {EVIDENCE_TYPES.length} attached
            </span>
          )}
        </div>
        {documentsError && <div className="alert alert-error">{documentsError}</div>}
        {/* The count said as a length rather than a number, so how far off
            being citable this project is reads before the sentence does. */}
        {!documentsLoading && !documentsError && (
          <div
            className="evidence-meter"
            role="img"
            aria-label={`${attached} of ${EVIDENCE_TYPES.length} evidence documents attached`}
          >
            <span
              className="evidence-meter-fill"
              style={{ width: `${(attached / EVIDENCE_TYPES.length) * 100}%` }}
            />
          </div>
        )}
        <p className="muted hint">{evidenceHint}</p>
        {/* While the document log is being re-read the marks below still
            show the previous answer. The sentence above says so; this
            keeps the pills from stating it at full strength. */}
        <div
          className={documentsLoading ? "evidence-busy" : undefined}
          aria-busy={documentsLoading || undefined}
        >
          <EvidenceStrip
            heldTypes={heldTypes}
            onMissingClick={onAddMissing}
            onHeldClick={onShowDocuments}
          />
        </div>
      </section>

      {/* Where this project has been put forward as experience. The Evidence
          card above says whether it *can* be cited; this says where it has
          been. Read from the tenders' own citation lists -- the project holds
          no list of its own and is not changed by being cited -- so it is
          reported here rather than presented as something about the project.

          Always rendered, like Scope & notes below: a section that vanishes
          when empty reads as a screen with no such field, where "not cited
          yet" is a real and useful answer. */}
      <section className="card">
        <div className="list-head">
          <h3>Cited by</h3>
          {!citingTendersLoading && !citingTendersError && citingTenders.length > 0 && (
            <span className="muted evidence-count">
              {citingTenders.length} {citingTenders.length === 1 ? "tender" : "tenders"}
            </span>
          )}
        </div>

        {citingTendersError ? (
          <div className="alert alert-error">{citingTendersError}</div>
        ) : citingTendersLoading && citingTenders.length === 0 ? (
          <TableSkeleton columns={citedByColumns} rows={2} />
        ) : (
          <DataTable
            columns={citedByColumns}
            rows={citingTenders}
            onRowClick={onOpenTender}
            empty={
              <EmptyState message="No tender cites this project yet. A tender cites it from its own Cited projects tab." />
            }
          />
        )}
      </section>

      {/* Always rendered, where it used to vanish when both were blank.
          A section that disappears does not read as "nothing recorded" --
          it reads as a screen that has no such field. */}
      <section className="card">
        <h3>Scope &amp; notes</h3>
        <div className="project-prose">
          <h4>Scope summary</h4>
          {project.scope_summary ? (
            <p className="prose">{project.scope_summary}</p>
          ) : (
            <p className="muted hint">Not recorded. Add it with Edit project.</p>
          )}
          <h4>Notes</h4>
          {project.notes ? (
            <p className="prose">{project.notes}</p>
          ) : (
            <p className="muted hint">Not recorded. Add them with Edit project.</p>
          )}
        </div>
      </section>

      <section className="card">
        <h3>Record</h3>
        <dl className="detail-facts detail-facts-wide">
          {record.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </>
  );
}

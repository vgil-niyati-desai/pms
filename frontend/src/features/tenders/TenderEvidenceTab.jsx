import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import EvidenceStrip from "../projects/EvidenceStrip";
import { dash, formatPeriod } from "../../lib/format";

/**
 * The past projects this bid puts forward as experience.
 *
 * A citation is a pointer and nothing more: the project is evidence in its
 * own right, is cited by as many bids as put it forward, and is not changed
 * by being cited. Removing it here removes the citation, never the project.
 *
 * Each row carries the same evidence strip the Projects screen shows, because
 * the question being asked of a cited project is exactly the one that strip
 * answers -- whether the documents that prove the work exist. Those documents
 * stay the project's; citing does not attach them to the tender, and they do
 * not appear on its Documents tab.
 */
export default function TenderEvidenceTab({
  projects,
  loading,
  error,
  onOpen,
  onCite,
  onRemove,
  removingId,
}) {
  // Only when there is nothing on screen yet; a reload after citing keeps the
  // rows in place, as the tabs beside this one do.
  const initialLoading = loading && projects.length === 0;

  const columns = [
    { key: "title", header: "Project", className: "cell-name" },
    { key: "client_name", header: "Client", render: (p) => dash(p.client_name) },
    {
      key: "period",
      header: "Period",
      className: "cell-tight",
      render: (p) => formatPeriod(p.start_date, p.end_date),
    },
    {
      key: "status",
      header: "Status",
      className: "cell-tight",
      render: (p) => <StatusPill value={p.status} />,
    },
    {
      key: "evidence",
      header: "Evidence held",
      render: (p) => <EvidenceStrip heldTypes={p.document_types || []} compact />,
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (project) => (
        <div className="row-actions">
          <button
            type="button"
            className="btn btn-sm btn-danger"
            disabled={Boolean(removingId)}
            onClick={() => onRemove(project)}
          >
            {removingId === project.id ? "Removing..." : "Remove"}
          </button>
        </div>
      ),
    },
  ];

  return (
    <section className="card">
      <div className="list-head">
        <h3>Cited projects {!initialLoading && `(${projects.length})`}</h3>
        <button type="button" className="btn btn-primary" onClick={onCite}>
          + Cite a project
        </button>
      </div>

      <p className="muted hint">
        Past work put forward as this bid&rsquo;s experience. Citing a project
        does not change it, and does not attach its documents to this tender.
      </p>

      {error && <div className="alert alert-error">{error}</div>}

      {initialLoading ? (
        <p className="muted">Loading...</p>
      ) : projects.length === 0 ? (
        <EmptyState
          message="No projects cited as evidence yet."
          action={
            <button type="button" className="btn btn-sm" onClick={onCite}>
              Cite the first project
            </button>
          }
        />
      ) : (
        <DataTable columns={columns} rows={projects} empty={null} onRowClick={onOpen} />
      )}
    </section>
  );
}

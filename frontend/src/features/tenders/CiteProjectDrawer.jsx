import { useCallback, useState } from "react";
import Drawer from "../../components/Drawer";
import EmptyState from "../../components/EmptyState";
import SearchInput from "../../components/SearchInput";
import StatusPill from "../../components/StatusPill";
import useResource from "../../hooks/useResource";
import { listProjects } from "../../api/projects";
import EvidenceStrip from "../projects/EvidenceStrip";
import { dash, formatPeriod } from "../../lib/format";

// A picker, not a browser: past this many results the search is what should
// be narrowed, not the list scrolled. The count above the list always tells
// the truth about how many matched. The same rule the document picker uses.
const PAGE_SIZE = 25;

/**
 * Cite a project that already exists as evidence for this tender.
 *
 * Searching reuses the projects list endpoint, the same one the Projects
 * screen reads, so a project is found here by the text it is found by there.
 * Nothing is created: citing points at the project that already exists, and
 * a project may be cited by any number of bids, so an already-cited row says
 * so rather than being refused.
 */
export default function CiteProjectDrawer({ citedIds, onCite, onClose }) {
  const [query, setQuery] = useState("");
  const [citingId, setCitingId] = useState(null);
  // Cited while this drawer was open. The tender's own list catches up once
  // its reload lands; until then these rows already read "Already cited".
  const [justCitedIds, setJustCitedIds] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(
    () => listProjects({ q: query, pageSize: PAGE_SIZE }),
    [query],
  );
  const projects = useResource(load, { initialData: { items: [], total: 0 } });

  const cited = new Set([...(citedIds || []), ...justCitedIds]);

  async function cite(project) {
    setError(null);
    setCitingId(project.id);
    try {
      await onCite(project.id);
      setJustCitedIds((ids) => [...ids, project.id]);
    } catch (err) {
      setError(err.message || "Could not cite this project.");
    } finally {
      // On success too: the drawer stays open for the next citation, and
      // while this is set every Cite button and the way out are disabled.
      setCitingId(null);
    }
  }

  const rows = projects.data?.items ?? [];
  const total = projects.data?.total ?? 0;

  return (
    <Drawer title="Cite a project" onClose={onClose} dismissable={!citingId}>
      <div className="attach-existing">
        {error && <div className="alert alert-error">{error}</div>}

        <p className="muted hint attach-existing-lead">
          Puts an existing project forward as this bid&rsquo;s past experience.
          The project is not changed, and its documents stay its own.
        </p>

        <SearchInput
          id="cite-project-search"
          label="Find a project"
          value={query}
          onChange={setQuery}
          placeholder="Title, client, reference no."
        />

        {projects.error && <div className="alert alert-error">{projects.error}</div>}

        {projects.loading && rows.length === 0 ? (
          <p className="muted">Loading...</p>
        ) : rows.length === 0 ? (
          <EmptyState
            message={
              query
                ? "No projects match that search."
                : "There are no projects to cite yet."
            }
            action={
              query ? (
                <button type="button" className="btn btn-sm" onClick={() => setQuery("")}>
                  Clear the search
                </button>
              ) : null
            }
          />
        ) : (
          <>
            <p className="muted hint">
              {total > rows.length
                ? `Showing the first ${rows.length} of ${total} matches — narrow the search to see the rest.`
                : `${total} ${total === 1 ? "project" : "projects"}`}
            </p>

            <ul className="attach-list">
              {rows.map((project) => {
                const isCited = cited.has(project.id);
                return (
                  <li key={project.id} className="attach-row">
                    <div className="attach-row-main">
                      <div className="attach-row-head">
                        <StatusPill value={project.status} />
                        <span className="attach-row-ref">{project.title}</span>
                      </div>
                      <p className="muted attach-row-meta">
                        {[
                          dash(project.client_name),
                          formatPeriod(project.start_date, project.end_date),
                        ]
                          .filter((part) => part && part !== "—")
                          .join(" · ") || "No details recorded"}
                      </p>
                      <EvidenceStrip heldTypes={project.document_types || []} compact />
                    </div>

                    <div className="attach-row-action">
                      {isCited ? (
                        <span className="tag tag-neutral">Already cited</span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={Boolean(citingId)}
                          onClick={() => cite(project)}
                        >
                          {citingId === project.id ? "Citing..." : "Cite"}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}

        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose} disabled={Boolean(citingId)}>
            Done
          </button>
        </div>
      </div>
    </Drawer>
  );
}

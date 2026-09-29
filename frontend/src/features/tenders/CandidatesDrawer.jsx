import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import Drawer from "../../components/Drawer";
import EmptyState from "../../components/EmptyState";
import FileViewLink from "../../components/FileViewLink";
import useResource from "../../hooks/useResource";
import { getCriterionCandidates } from "../../api/tenders";
import { paths } from "../../app/routes";
import { criterionSummary, kindLabel } from "./criteriaFields";

const OWNER_PATH = {
  project: paths.project,
  employee: paths.employee,
  tender: paths.tender,
};

/**
 * The records that meet one criterion, by the fixed rules the backend
 * applies to what is recorded (app/matching.py). Each result says why it is
 * listed and what could not be checked; the notes above the list say what
 * the rules cannot decide at all. Nothing is ranked or scored, and nothing is
 * stored: a project can be cited from here, as from the Evidence tab, and a
 * document linked to the tender, as from the Documents tab -- each only when
 * the person asks. People are suggestions only.
 */
export default function CandidatesDrawer({ tender, criterion, onCite, onLinkDocument, onClose }) {
  const [citingId, setCitingId] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(
    () => getCriterionCandidates(tender.id, criterion.id),
    [tender.id, criterion.id],
  );
  const results = useResource(load);
  const data = results.data;

  async function link(candidate) {
    setError(null);
    setCitingId(candidate.id);
    try {
      await onLinkDocument(candidate.id);
      results.reload();
    } catch (err) {
      setError(err.message || "Could not link this document to the tender.");
    } finally {
      setCitingId(null);
    }
  }

  async function cite(candidate) {
    setError(null);
    setCitingId(candidate.id);
    try {
      await onCite(candidate.id);
      results.reload();
    } catch (err) {
      setError(err.message || "Could not cite this project.");
    } finally {
      setCitingId(null);
    }
  }

  function action(candidate) {
    if (candidate.type === "project") {
      return candidate.cited ? (
        <span className="tag tag-neutral">Already cited</span>
      ) : (
        <button
          type="button"
          className="btn btn-sm"
          disabled={Boolean(citingId)}
          onClick={() => cite(candidate)}
        >
          {citingId === candidate.id ? "Citing..." : "Cite"}
        </button>
      );
    }
    if (candidate.type === "employee") {
      return <Link to={paths.employee(candidate.id)}>Open employee</Link>;
    }
    return (
      <div className="row-actions">
        <FileViewLink
          documentId={candidate.file_name ? candidate.id : null}
          fileName={candidate.file_name}
          title={candidate.title}
        />
        {candidate.attached ? (
          <span className="tag tag-neutral">Linked to this tender</span>
        ) : (
          <button
            type="button"
            className="btn btn-sm"
            disabled={Boolean(citingId)}
            onClick={() => link(candidate)}
          >
            {citingId === candidate.id ? "Linking..." : "Link to tender"}
          </button>
        )}
      </div>
    );
  }

  return (
    <Drawer title="Find candidates" onClose={onClose} dismissable={!citingId}>
      <div className="attach-existing">
        <p className="muted hint drawer-context">
          {kindLabel(criterion.kind)} &middot; {criterionSummary(criterion)}
        </p>

        {error && <div className="alert alert-error">{error}</div>}
        {results.error && <div className="alert alert-error">{results.error}</div>}

        {results.loading && !data ? (
          <p className="muted">Looking for candidates...</p>
        ) : data ? (
          <>
            {data.mode === "manual" && (
              <p>
                <strong>Not enough data</strong> to suggest candidates for this criterion.
              </p>
            )}
            {data.notes.map((note) => (
              <div className="alert" key={note}>
                {note}
              </div>
            ))}

            {data.mode === "matched" && (
              <>
                {data.summary && <p className="muted hint">{data.summary}</p>}
                {data.truncated && (
                  <p className="muted hint">
                    Showing the first {data.candidates.length} of {data.total}.
                  </p>
                )}
                {data.candidates.length === 0 ? (
                  <EmptyState message="Nothing recorded passes these rules." />
                ) : (
                  <ul className="attach-list">
                    {data.candidates.map((candidate) => (
                      <li key={`${candidate.type}-${candidate.id}`} className="attach-row">
                        <div className="attach-row-main">
                          <div className="attach-row-head">
                            {candidate.type === "project" ? (
                              <Link className="attach-row-ref" to={paths.project(candidate.id)}>
                                {candidate.title}
                              </Link>
                            ) : (
                              <span className="attach-row-ref">{candidate.title}</span>
                            )}
                          </div>
                          {candidate.subtitle && (
                            <p className="muted attach-row-meta">{candidate.subtitle}</p>
                          )}
                          {candidate.owner_type && OWNER_PATH[candidate.owner_type] && (
                            <p className="muted attach-row-meta">
                              Belongs to{" "}
                              <Link to={OWNER_PATH[candidate.owner_type](candidate.owner_id)}>
                                {candidate.owner_label}
                              </Link>
                            </p>
                          )}
                          {candidate.reasons.map((reason) => (
                            <p className="attach-row-meta" key={`r-${reason}`}>
                              ✓ {reason}
                            </p>
                          ))}
                          {candidate.missing.map((gap) => (
                            <p className="muted attach-row-meta" key={`m-${gap}`}>
                              ✗ {gap}
                            </p>
                          ))}
                        </div>
                        <div className="attach-row-action">{action(candidate)}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        ) : null}

        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose} disabled={Boolean(citingId)}>
            Done
          </button>
        </div>
      </div>
    </Drawer>
  );
}

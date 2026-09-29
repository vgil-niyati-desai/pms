import { useCallback, useEffect, useState } from "react";
import EmptyState from "../../components/EmptyState";
import SearchInput from "../../components/SearchInput";
import StatusPill from "../../components/StatusPill";
import useResource from "../../hooks/useResource";
import { listDocuments } from "../../api/documents";
import { getProject } from "../../api/projects";
import { dash } from "../../lib/format";

// A picker, not a browser: past this many results the search is what should
// be narrowed, not the list scrolled. The count above the list always tells
// the truth about how many matched.
const SHOWN = 25;

/**
 * Attach a document that is already in the log to this project -- or, given
 * `tenderId` instead, link one to this tender.
 *
 * The other half of the Add Document drawer. "Upload new" creates a record
 * and a file; this attaches an existing record by its existing id, so nothing
 * is duplicated -- no second document row, no second copy of the file.
 *
 * Searching reuses the document log's own endpoint, the same one the log
 * screen uses. Each row says whether it can be attached, because two states
 * mean it cannot:
 *
 *   * already attached here -- attaching again would be a no-op, so the row
 *     says so rather than offering a button that does nothing
 *   * held by another project -- attaching would either move it out of a
 *     project still showing it or leave two projects claiming it, so it is
 *     refused here and, if it is claimed between this list loading and the
 *     button being pressed, again by the server
 *
 * A tender only lists what it links to. A project's evidence, a person's
 * certificate or another bid's paper can be put forward by this tender and
 * stays where it is, so in tender mode none of those is refused -- the row
 * says where the document stays instead.
 */
export default function AttachExistingDocument({
  projectId,
  tenderId,
  attachedIds,
  onAttach,
  onCancel,
  busy = false,
}) {
  const shared = Boolean(tenderId);
  const [query, setQuery] = useState("");
  const [attachingId, setAttachingId] = useState(null);
  const [error, setError] = useState(null);
  // { [projectId]: { title } | null }. null = looked up and not there.
  const [owners, setOwners] = useState({});

  // Only the rows shown are fetched; the log is paged on the server, and the
  // total says how many matched in all.
  const load = useCallback(() => listDocuments({ q: query, pageSize: SHOWN }), [query]);
  const documents = useResource(load, { initialData: { items: [], total: 0 } });

  const attached = new Set(attachedIds || []);

  async function attach(document) {
    setError(null);
    setAttachingId(document.id);
    try {
      await onAttach(document.id);
    } catch (err) {
      setError(err.message || "Could not attach this document.");
      setAttachingId(null);
    }
  }

  const shown = documents.data?.items ?? [];
  const total = documents.data?.total ?? 0;
  const disabled = busy || Boolean(attachingId);

  // The projects holding the documents on screen, so a row can name where
  // to go and detach one rather than just saying it is spoken for. Only
  // the ids actually shown are looked up -- usually none or one or two --
  // so this stays a handful of small reads, not a fetch of every project.
  // Not needed for a tender, which refuses nothing a project holds.
  const foreignIds = shared
    ? []
    : [
        ...new Set(
          shown
            .map((d) => d.project_id)
            .filter((id) => id && id !== projectId),
        ),
      ];
  const foreignKey = foreignIds.join(",");

  useEffect(() => {
    if (!foreignKey) return undefined;
    let live = true;
    Promise.all(
      foreignKey.split(",").map((id) =>
        getProject(id)
          .then((project) => [id, { title: project.title }])
          // null means the project is gone, which is exactly what the
          // server treats as a stale claim -- so the row becomes
          // attachable rather than being blocked by a ghost.
          .catch(() => [id, null]),
      ),
    ).then((pairs) => {
      if (live) setOwners((prev) => ({ ...prev, ...Object.fromEntries(pairs) }));
    });
    return () => {
      live = false;
    };
  }, [foreignKey]);

  return (
    <div className="attach-existing">
      {error && <div className="alert alert-error">{error}</div>}

      <p className="muted hint attach-existing-lead">
        Attaches a document already in the log, using the record it already
        has. Nothing is uploaded and no second copy is made.
        {shared && " A document another project, person or tender holds stays with them; this tender only lists it."}
      </p>

      <SearchInput
        id="attach-search"
        label="Find a document"
        value={query}
        onChange={setQuery}
        placeholder="Client, project title, reference no."
      />

      {documents.error && <div className="alert alert-error">{documents.error}</div>}

      {documents.loading && shown.length === 0 ? (
        <p className="muted">Loading...</p>
      ) : shown.length === 0 ? (
        <EmptyState
          message={
            query
              ? "No documents in the log match that search."
              : "There are no documents in the log yet."
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
            {total > shown.length
              ? `Showing the first ${shown.length} of ${total} matches — narrow the search to see the rest.`
              : `${total} ${total === 1 ? "document" : "documents"}`}
          </p>

          <ul className="attach-list">
            {shown.map((document) => {
              const isAttached = shared
                ? attached.has(document.id) || document.tender_id === tenderId
                : attached.has(document.id) || document.project_id === projectId;
              const owner = document.project_id ? owners[document.project_id] : undefined;
              // A claim on a project that no longer exists holds nothing, and
              // the server allows the attach, so the row must offer it too.
              const elsewhere =
                !shared && !isAttached && Boolean(document.project_id) && owner !== null;
              const staysWith = !shared
                ? null
                : document.project_id
                  ? "Stays with its project"
                  : document.employee_id
                    ? "Stays with its employee"
                    : document.tender_id && document.tender_id !== tenderId
                      ? "Also listed by another tender"
                      : null;

              return (
                <li key={document.id} className="attach-row">
                  <div className="attach-row-main">
                    <div className="attach-row-head">
                      <StatusPill value={document.document_type} />
                      <span className="attach-row-ref">{dash(document.reference_number)}</span>
                    </div>
                    <p className="muted attach-row-meta">
                      {[document.client_name, document.project_title, document.document_date]
                        .filter(Boolean)
                        .join(" · ") || "No details recorded"}
                    </p>
                    {document.file_name && (
                      <p className="muted attach-row-file">{document.file_name}</p>
                    )}
                    {staysWith && !isAttached && <p className="muted attach-row-meta">{staysWith}</p>}
                  </div>

                  <div className="attach-row-action">
                    {isAttached ? (
                      <span className="tag tag-neutral">Already attached</span>
                    ) : elsewhere ? (
                      <span
                        className="tag tag-neutral"
                        title="Detach it from that project first, or upload a copy here"
                      >
                        {/* Until the lookup lands the project is known to
                            exist but not by name, so the row says the part
                            it is sure of. */}
                        {owner ? `In ${owner.title}` : "In another project"}
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={disabled}
                        onClick={() => attach(document)}
                      >
                        {attachingId === document.id
                          ? shared ? "Linking..." : "Attaching..."
                          : shared ? "Link" : "Attach"}
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
        <button type="button" className="btn" onClick={onCancel} disabled={disabled}>
          Cancel
        </button>
      </div>
    </div>
  );
}

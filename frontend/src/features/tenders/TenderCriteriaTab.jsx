import { useState } from "react";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import StatusPill from "../../components/StatusPill";
import ConfirmDelete from "../../components/ConfirmDelete";
import { deleteCriterion } from "../../api/tenders";
import { dash } from "../../lib/format";
import {
  CRITERION_CATEGORIES,
  criteriaCountLabel,
  criterionSummary,
  isMatchable,
  kindLabel,
  priorityLabel,
} from "./criteriaFields";

/**
 * What the tender asks of a bidder, grouped the way a notice usually is:
 * technical experience, personnel, financial standing, documents, and
 * whatever does not fit those.
 *
 * Each row reads as one line of requirement, with the clause as published
 * underneath it. Nothing here says whether a requirement is met. Where a
 * kind can be looked up in recorded data, Find candidates lists the records
 * that pass its rules; the rest are checked by hand.
 */
export default function TenderCriteriaTab({ tender, onOpen, onAdd, onChanged, onFindCandidates }) {
  const criteria = tender.criteria || [];
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteCriterion(tender.id, pendingDelete.id);
      setPendingDelete(null);
      onChanged();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this criterion.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = [
    {
      key: "requirement",
      header: "Requirement",
      className: "cell-name",
      render: (criterion) => (
        <>
          <span>{criterionSummary(criterion)}</span>
          <p className="muted hint">{kindLabel(criterion.kind)}</p>
          {criterion.source_text && <p className="muted hint">&ldquo;{criterion.source_text}&rdquo;</p>}
          {criterion.notes && <p className="muted hint">Note: {criterion.notes}</p>}
        </>
      ),
    },
    {
      key: "clause_ref",
      header: "Clause",
      className: "cell-tight",
      render: (criterion) => dash(criterion.clause_ref),
    },
    {
      key: "priority",
      header: "Requirement type",
      className: "cell-tight",
      render: (criterion) => <StatusPill value={priorityLabel(criterion)} />,
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (criterion) => (
        <div className="row-actions">
          {isMatchable(criterion) ? (
            <button type="button" className="btn btn-sm" onClick={() => onFindCandidates(criterion)}>
              Find candidates
            </button>
          ) : (
            <span className="muted hint">Manual check</span>
          )}
          <button type="button" className="btn btn-sm" onClick={() => onOpen(criterion)}>
            Edit
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger"
            onClick={() => {
              setDeleteError(null);
              setPendingDelete(criterion);
            }}
          >
            Delete
          </button>
        </div>
      ),
    },
  ];

  const groups = CRITERION_CATEGORIES.map((category) => ({
    ...category,
    items: criteria.filter((criterion) => criterion.category === category.key),
  })).filter((group) => group.items.length > 0);

  return (
    <>
      <section className="card">
        <div className="list-head">
          <h3>Qualification criteria</h3>
          <button type="button" className="btn btn-primary" onClick={onAdd}>
            + Add criterion
          </button>
        </div>
        {criteria.length === 0 ? (
          <EmptyState
            message="No qualification criteria recorded yet. Add them from the tender notice so evidence can be gathered against them."
            action={
              <button type="button" className="btn btn-sm" onClick={onAdd}>
                Add the first criterion
              </button>
            }
          />
        ) : (
          <p className="muted hint">{criteriaCountLabel(criteria)}</p>
        )}
      </section>

      {groups.map((group) => (
        <section className="card" key={group.key}>
          <div className="list-head">
            <h3>
              {group.label} ({group.items.length})
            </h3>
          </div>
          <DataTable columns={columns} rows={group.items} onRowClick={onOpen} />
        </section>
      ))}

      {pendingDelete && (
        <ConfirmDelete
          title="Delete this criterion?"
          confirmLabel="Delete criterion"
          busy={deleting}
          error={deleteError}
          onConfirm={confirmDelete}
          onCancel={() => {
            setPendingDelete(null);
            setDeleteError(null);
          }}
        >
          <p className="muted">{criterionSummary(pendingDelete)}</p>
        </ConfirmDelete>
      )}
    </>
  );
}

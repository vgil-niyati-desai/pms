import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import TagInput from "../../components/TagInput";
import ConfirmDelete from "../../components/ConfirmDelete";
import useResource from "../../hooks/useResource";
import {
  EMPTY_TENDER,
  TENDER_STATUSES,
  createTender,
  deleteTender,
  getTender,
  listTags,
  updateTender,
} from "../../api/tenders";
import { TENDER_TYPES, SUBMISSION_MODES } from "./tenderFields";
import { paths } from "../../app/routes";

/** Pulls just the editable fields off a record, so ids and timestamps stay put. */
function formFromTender(tender) {
  const next = { ...EMPTY_TENDER };
  for (const key of Object.keys(EMPTY_TENDER)) {
    next[key] = tender[key] ?? EMPTY_TENDER[key];
  }
  return next;
}

export default function TenderFormPage() {
  const { tenderId } = useParams();
  const navigate = useNavigate();
  const isEditing = Boolean(tenderId);

  const [form, setForm] = useState(EMPTY_TENDER);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const loadTender = useCallback(
    () => (isEditing ? getTender(tenderId) : Promise.resolve(null)),
    [isEditing, tenderId],
  );
  const existing = useResource(loadTender);

  const loadTags = useCallback(() => listTags(), []);
  const tags = useResource(loadTags, { initialData: [] });

  useEffect(() => {
    if (existing.data) setForm(formFromTender(existing.data));
  }, [existing.data]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!form.title.trim() || !form.issuing_authority.trim() || !form.status) {
      setError("Please fill in Tender title, Issuing authority, and Status.");
      return;
    }
    if (
      form.published_date &&
      form.submission_deadline &&
      form.submission_deadline < form.published_date
    ) {
      setError("The submission deadline cannot be before the published date.");
      return;
    }

    setSubmitting(true);
    try {
      const saved = isEditing ? await updateTender(tenderId, form) : await createTender(form);
      navigate(paths.tender(saved.id), { replace: true });
    } catch (err) {
      setError(err.message || "Could not save this tender.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteTender(tenderId);
      navigate(paths.tenders(), { replace: true });
    } catch (err) {
      setDeleteError(err.message || "Could not delete this tender.");
      setDeleting(false);
    }
  }

  // Only an edit has something to wait for; the create form is ready at once.
  if (isEditing && existing.loading) return <p className="muted">Loading...</p>;
  if (existing.error) {
    return (
      <div className="card">
        <div className="alert alert-error">{existing.error}</div>
        <Link to={paths.tenders()}>Back to Tenders</Link>
      </div>
    );
  }

  const costCount = existing.data?.cost_items?.length ?? 0;
  const certificateCount = existing.data?.certificates?.length ?? 0;

  return (
    <>
      <form className="card form" onSubmit={handleSubmit}>
        <h2>{isEditing ? "Edit tender" : "New tender"}</h2>

        {error && <div className="alert alert-error">{error}</div>}

        <FormRow>
          <Field label="Tender title *" htmlFor="title">
            <input id="title" name="title" type="text" value={form.title} onChange={handleChange} />
          </Field>
          <Field label="Issuing authority *" htmlFor="issuing_authority">
            <input
              id="issuing_authority"
              name="issuing_authority"
              type="text"
              value={form.issuing_authority}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Tender reference no." htmlFor="reference_number">
            <input
              id="reference_number"
              name="reference_number"
              type="text"
              value={form.reference_number}
              onChange={handleChange}
            />
          </Field>
          <Field label="Tender type" htmlFor="tender_type">
            <select
              id="tender_type"
              name="tender_type"
              value={form.tender_type}
              onChange={handleChange}
            >
              <option value="">Not set</option>
              {TENDER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Estimated value" htmlFor="estimated_value" hint="Numbers only.">
            <input
              id="estimated_value"
              name="estimated_value"
              type="number"
              min="0"
              value={form.estimated_value}
              onChange={handleChange}
            />
          </Field>
          <Field label="EMD amount" htmlFor="emd_amount">
            <input
              id="emd_amount"
              name="emd_amount"
              type="number"
              min="0"
              value={form.emd_amount}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Tender fee" htmlFor="tender_fee">
            <input
              id="tender_fee"
              name="tender_fee"
              type="number"
              min="0"
              value={form.tender_fee}
              onChange={handleChange}
            />
          </Field>
          <Field label="Status *" htmlFor="status">
            <select id="status" name="status" value={form.status} onChange={handleChange}>
              <option value="">Select status</option>
              {TENDER_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Published date" htmlFor="published_date">
            <input
              id="published_date"
              name="published_date"
              type="date"
              value={form.published_date}
              onChange={handleChange}
            />
          </Field>
          <Field label="Submission deadline" htmlFor="submission_deadline">
            <input
              id="submission_deadline"
              name="submission_deadline"
              type="date"
              value={form.submission_deadline}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <Field label="Submission mode" htmlFor="submission_mode">
          <select
            id="submission_mode"
            name="submission_mode"
            value={form.submission_mode}
            onChange={handleChange}
          >
            <option value="">Not set</option>
            {SUBMISSION_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </Field>

        <TagInput
          value={form.tags}
          suggestions={tags.data}
          onChange={(next) => setForm((prev) => ({ ...prev, tags: next }))}
          hint="Used to find this tender again later."
        />

        <Field label="Notes" htmlFor="notes">
          <textarea id="notes" name="notes" rows={3} value={form.notes} onChange={handleChange} />
        </Field>

        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? "Saving..." : isEditing ? "Save changes" : "Create tender"}
          </button>
          <Link className="btn" to={isEditing ? paths.tender(tenderId) : paths.tenders()}>
            Cancel
          </Link>
          {isEditing && (
            <button
              type="button"
              className="btn btn-danger form-actions-end"
              onClick={() => setConfirmingDelete(true)}
              disabled={submitting}
            >
              Delete tender
            </button>
          )}
        </div>
      </form>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this tender?"
          confirmLabel="Delete tender"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">{form.title || "Untitled tender"}</p>
          <p className="muted">
            The tender record will be permanently removed, along with {costCount}{" "}
            {costCount === 1 ? "cost item" : "cost items"} and {certificateCount}{" "}
            {certificateCount === 1 ? "certificate" : "certificates"}. Files already uploaded to
            the server, including its documents, are not deleted.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

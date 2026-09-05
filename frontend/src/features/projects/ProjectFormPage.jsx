import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import TagInput from "../../components/TagInput";
import ConfirmDelete from "../../components/ConfirmDelete";
import useResource from "../../hooks/useResource";
import {
  EMPTY_PROJECT,
  PROJECT_STATUSES,
  createProject,
  deleteProject,
  getProject,
  listTags,
  updateProject,
} from "../../api/projects";
import { paths } from "../../app/routes";

/** Pulls just the editable fields off a record, so ids and timestamps stay put. */
function formFromProject(project) {
  const next = { ...EMPTY_PROJECT };
  for (const key of Object.keys(EMPTY_PROJECT)) {
    next[key] = project[key] ?? EMPTY_PROJECT[key];
  }
  return next;
}

export default function ProjectFormPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const isEditing = Boolean(projectId);

  const [form, setForm] = useState(EMPTY_PROJECT);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const loadProject = useCallback(
    () => (isEditing ? getProject(projectId) : Promise.resolve(null)),
    [isEditing, projectId],
  );
  const existing = useResource(loadProject);

  const loadTags = useCallback(() => listTags(), []);
  const tags = useResource(loadTags, { initialData: [] });

  useEffect(() => {
    if (existing.data) setForm(formFromProject(existing.data));
  }, [existing.data]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!form.title.trim() || !form.client_name.trim()) {
      setError("Please fill in Project title and Client / organization.");
      return;
    }
    if (form.start_date && form.end_date && form.start_date > form.end_date) {
      setError("End date cannot be before start date.");
      return;
    }

    setSubmitting(true);
    try {
      const saved = isEditing
        ? await updateProject(projectId, form)
        : await createProject(form);
      navigate(paths.project(saved.id), { replace: true });
    } catch (err) {
      setError(err.message || "Could not save this project.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(projectId);
      navigate(paths.projects(), { replace: true });
    } catch (err) {
      setDeleteError(err.message || "Could not delete this project.");
      setDeleting(false);
    }
  }

  // Only an edit has something to wait for; the create form is ready at once.
  if (isEditing && existing.loading) return <p className="muted">Loading...</p>;
  if (existing.error) {
    return (
      <div className="card">
        <div className="alert alert-error">{existing.error}</div>
        <Link to={paths.projects()}>Back to Projects</Link>
      </div>
    );
  }

  const documentCount = existing.data?.document_ids?.length ?? 0;

  return (
    <>
      <form className="card form" onSubmit={handleSubmit}>
        <h2>{isEditing ? "Edit project" : "New project"}</h2>

        {error && <div className="alert alert-error">{error}</div>}

        <FormRow>
          <Field label="Project title *" htmlFor="title">
            <input id="title" name="title" type="text" value={form.title} onChange={handleChange} />
          </Field>
          <Field label="Client / organization *" htmlFor="client_name">
            <input
              id="client_name"
              name="client_name"
              type="text"
              value={form.client_name}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Reference number" htmlFor="reference_number">
            <input
              id="reference_number"
              name="reference_number"
              type="text"
              value={form.reference_number}
              onChange={handleChange}
            />
          </Field>
          <Field label="Contract value" htmlFor="contract_value">
            <input
              id="contract_value"
              name="contract_value"
              type="text"
              value={form.contract_value}
              onChange={handleChange}
              placeholder="e.g. 5,00,000"
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Start date" htmlFor="start_date">
            <input
              id="start_date"
              name="start_date"
              type="date"
              value={form.start_date}
              onChange={handleChange}
            />
          </Field>
          <Field label="End date" htmlFor="end_date">
            <input
              id="end_date"
              name="end_date"
              type="date"
              value={form.end_date}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Status" htmlFor="status">
            <select id="status" name="status" value={form.status} onChange={handleChange}>
              <option value="">Not set</option>
              {PROJECT_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Department / domain" htmlFor="department">
            <input
              id="department"
              name="department"
              type="text"
              value={form.department}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <TagInput
          value={form.tags}
          suggestions={tags.data}
          onChange={(next) => setForm((prev) => ({ ...prev, tags: next }))}
          hint="Used to find this project again when assembling a tender."
        />

        <Field label="Scope summary" htmlFor="scope_summary">
          <textarea
            id="scope_summary"
            name="scope_summary"
            rows={3}
            value={form.scope_summary}
            onChange={handleChange}
          />
        </Field>

        <Field label="Notes" htmlFor="notes">
          <textarea id="notes" name="notes" rows={3} value={form.notes} onChange={handleChange} />
        </Field>

        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? "Saving..." : isEditing ? "Save changes" : "Create project"}
          </button>
          <Link
            className="btn"
            to={isEditing ? paths.project(projectId) : paths.projects()}
          >
            Cancel
          </Link>
          {isEditing && (
            <button
              type="button"
              className="btn btn-danger form-actions-end"
              onClick={() => setConfirmingDelete(true)}
              disabled={submitting}
            >
              Delete project
            </button>
          )}
        </div>
      </form>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this project?"
          confirmLabel="Delete project"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">{form.title || "Untitled project"}</p>
          <p className="muted">
            The project record will be permanently removed. Its {documentCount}{" "}
            {documentCount === 1 ? "document stays" : "documents stay"} in the document log
            and {documentCount === 1 ? "is" : "are"} not deleted.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

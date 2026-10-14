import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FormSkeleton from "../../components/FormSkeleton";
import TagInput from "../../components/TagInput";
import ConfirmDelete from "../../components/ConfirmDelete";
import ConfirmDiscard from "../../components/ConfirmDiscard";
import useResource from "../../hooks/useResource";
import useUnsavedChanges from "../../hooks/useUnsavedChanges";
import useFieldErrors from "../../hooks/useFieldErrors";
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
  const fieldErrors = useFieldErrors();
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

  // Where Cancel goes, and whether it should ask first.
  const cancelTo = isEditing ? paths.project(projectId) : paths.projects();
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  // The baseline is derived from the record rather than captured: the effect
  // below loads it a render late, so a captured one would be the blank form
  // and the loaded project would read as somebody's unsaved change. Before it
  // arrives -- and on a new project -- the blank form is its own baseline.
  // `release` comes before every navigation this page makes on purpose, so
  // the app-wide guard does not stop the page's own save, delete or discard.
  const { dirty, release } = useUnsavedChanges(
    form,
    existing.data ? formFromProject(existing.data) : EMPTY_PROJECT,
  );

  function requestCancel() {
    if (dirty) {
      setConfirmingLeave(true);
      return;
    }
    navigate(cancelTo);
  }

  useEffect(() => {
    if (existing.data) setForm(formFromProject(existing.data));
  }, [existing.data]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    fieldErrors.clear(name);
    // The date rule is about the pair, so editing either side settles it.
    if (name === "start_date") fieldErrors.clear("end_date");
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    const problems = {};
    if (!form.title.trim()) problems.title = "Please fill in Project title.";
    if (!form.client_name.trim()) problems.client_name = "Please fill in Client / organization.";
    if (form.start_date && form.end_date && form.start_date > form.end_date) {
      problems.end_date = "End date cannot be before start date.";
    }
    if (fieldErrors.report(problems)) return;

    setSubmitting(true);
    try {
      const saved = isEditing
        ? await updateProject(projectId, form)
        : await createProject(form);
      // Confirmed on the record screen, since that is where this lands.
      release();
      navigate(paths.project(saved.id), {
        replace: true,
        state: {
          notice: isEditing ? "Project saved." : "Project created.",
        },
      });
    } catch (err) {
      setError(err.message || "Could not save this project.");
      setSubmitting(false);
    }
  }

  // Read by handleDelete below as well as by the dialog, so it is declared
  // before both rather than between them.
  const documentCount = existing.data?.document_ids?.length ?? 0;

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(projectId);
      // The record is gone, so the confirmation belongs on the list.
      release();
      navigate(paths.projects(), {
        replace: true,
        state: {
          notice: `Project deleted. Its ${documentCount === 1 ? "document stays" : "documents stay"} in the document log.`,
        },
      });
    } catch (err) {
      setDeleteError(err.message || "Could not delete this project.");
      setDeleting(false);
    }
  }

  // Only an edit has something to wait for; the create form is ready at once.
  // Title/client, reference/contract value, start/end date, status/department
  // -- then the tag input and the two textareas below it.
  if (isEditing && existing.loading) {
    return <FormSkeleton rows={4} wide={["control", "textarea", "textarea"]} />;
  }
  if (existing.error) {
    return (
      <div className="card">
        <div className="alert alert-error">{existing.error}</div>
        <Link to={paths.projects()}>Back to Projects</Link>
      </div>
    );
  }


  return (
    <>
      <form className="card form" onSubmit={handleSubmit}>
        <h2>{isEditing ? "Edit project" : "New project"}</h2>

        {fieldErrors.summary && <div className="alert alert-error">{fieldErrors.summary}</div>}
        {error && <div className="alert alert-error">{error}</div>}

        <FormRow>
          <Field label="Project title *" htmlFor="title" required error={fieldErrors.errors.title}>
            <input id="title" name="title" type="text" value={form.title} onChange={handleChange} />
          </Field>
          <Field
            label="Client / organization *"
            htmlFor="client_name"
            required
            error={fieldErrors.errors.client_name}
          >
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
          <Field label="End date" htmlFor="end_date" error={fieldErrors.errors.end_date}>
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
          {/* A button, not a link: leaving with something unsaved asks first,
              and a Link would already be gone by the time it could. */}
          <button
            type="button"
            className="btn"
            onClick={requestCancel}
            disabled={submitting}
          >
            Cancel
          </button>
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

      {confirmingLeave && (
        <ConfirmDiscard
          body="This project has changes that have not been saved. Leaving now loses them."
          onKeep={() => setConfirmingLeave(false)}
          onDiscard={() => {
            setConfirmingLeave(false);
            release();
            navigate(cancelTo);
          }}
        />
      )}

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

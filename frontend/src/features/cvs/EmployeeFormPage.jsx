import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FormSkeleton from "../../components/FormSkeleton";
import ConfirmDelete from "../../components/ConfirmDelete";
import ConfirmDiscard from "../../components/ConfirmDiscard";
import useResource from "../../hooks/useResource";
import useUnsavedChanges from "../../hooks/useUnsavedChanges";
import useFieldErrors from "../../hooks/useFieldErrors";
import {
  EMPTY_EMPLOYEE,
  createEmployee,
  deleteEmployee,
  getEmployee,
  updateEmployee,
} from "../../api/employees";
import { paths } from "../../app/routes";
import { formatExperience } from "../../lib/format";

/** Pulls just the editable fields off a record, so ids and timestamps stay put. */
function formFromEmployee(employee) {
  const next = { ...EMPTY_EMPLOYEE };
  for (const key of Object.keys(EMPTY_EMPLOYEE)) {
    next[key] = employee[key] ?? EMPTY_EMPLOYEE[key];
  }
  return next;
}

/**
 * What a date will show as, right now, appended to the field's hint.
 *
 * The point of entering a date instead of a number is that the years look
 * after themselves — but that also means the person typing never sees the
 * figure they are setting. This puts it back in front of them as they type,
 * so a mistyped year is obvious immediately rather than at tender time.
 */
function experienceHint(value) {
  if (!value) return "";
  const started = new Date(`${value}T00:00:00`);
  if (Number.isNaN(started.getTime())) return "";
  const years = (Date.now() - started.getTime()) / (365.2425 * 24 * 60 * 60 * 1000);
  if (years < 0) return " Starts in the future — shows as none yet.";
  return ` Shows as ${formatExperience(years)} today, and updates on its own.`;
}

export default function EmployeeFormPage() {
  const { employeeId } = useParams();
  const navigate = useNavigate();
  const isEditing = Boolean(employeeId);

  const [form, setForm] = useState(EMPTY_EMPLOYEE);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const fieldErrors = useFieldErrors();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const loadEmployee = useCallback(
    () => (isEditing ? getEmployee(employeeId) : Promise.resolve(null)),
    [isEditing, employeeId],
  );
  const existing = useResource(loadEmployee);

  // Where Cancel goes, and whether it should ask first.
  const cancelTo = isEditing ? paths.employee(employeeId) : paths.cvs();
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  // The baseline is derived from the record rather than captured: the effect
  // below loads it a render late, so a captured one would be the blank form
  // and the loaded employee would read as somebody's unsaved change. Before it
  // arrives -- and on a new employee -- the blank form is its own baseline.
  // `release` comes before every navigation this page makes on purpose, so
  // the app-wide guard does not stop the page's own save, delete or discard.
  const { dirty, release } = useUnsavedChanges(
    form,
    existing.data ? formFromEmployee(existing.data) : EMPTY_EMPLOYEE,
  );

  function requestCancel() {
    if (dirty) {
      setConfirmingLeave(true);
      return;
    }
    navigate(cancelTo);
  }

  useEffect(() => {
    if (existing.data) setForm(formFromEmployee(existing.data));
  }, [existing.data]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    fieldErrors.clear(name);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    const problems = {};
    if (!form.full_name.trim()) problems.full_name = "Please fill in Full name.";
    if (!form.designation.trim()) problems.designation = "Please fill in Designation.";
    if (fieldErrors.report(problems)) return;

    setSubmitting(true);
    try {
      const saved = isEditing
        ? await updateEmployee(employeeId, form)
        : await createEmployee(form);
      // Confirmed on the record screen, since that is where this lands.
      release();
      navigate(paths.employee(saved.id), {
        replace: true,
        state: {
          notice: isEditing ? "Employee saved." : "Employee created.",
        },
      });
    } catch (err) {
      setError(err.message || "Could not save this employee.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteEmployee(employeeId);
      // The record is gone, so the confirmation belongs on the list.
      release();
      navigate(paths.cvs(), {
        replace: true,
        state: {
          notice: "Employee deleted. Files already uploaded stay in the document log.",
        },
      });
    } catch (err) {
      setDeleteError(err.message || "Could not delete this employee.");
      setDeleting(false);
    }
  }

  // Only an edit has something to wait for; the create form is ready at once.
  // Name/code, designation/department, the two experience dates,
  // qualification/email, phone/skills -- then notes below it.
  if (isEditing && existing.loading) {
    return <FormSkeleton rows={5} wide={["textarea"]} />;
  }
  if (existing.error) {
    return (
      <div className="card">
        <div className="alert alert-error">{existing.error}</div>
        <Link to={paths.cvs()}>Back to CVs</Link>
      </div>
    );
  }

  const cvCount = existing.data?.cvs?.length ?? 0;
  const certificationCount = existing.data?.certifications?.length ?? 0;

  return (
    <>
      <form className="card form" onSubmit={handleSubmit}>
        <h2>{isEditing ? "Edit employee" : "New employee"}</h2>

        {fieldErrors.summary && <div className="alert alert-error">{fieldErrors.summary}</div>}
        {error && <div className="alert alert-error">{error}</div>}

        <FormRow>
          <Field label="Full name *" htmlFor="full_name" required error={fieldErrors.errors.full_name}>
            <input
              id="full_name"
              name="full_name"
              type="text"
              value={form.full_name}
              onChange={handleChange}
            />
          </Field>
          <Field label="Employee code" htmlFor="employee_code">
            <input
              id="employee_code"
              name="employee_code"
              type="text"
              value={form.employee_code}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field
            label="Designation *"
            htmlFor="designation"
            required
            error={fieldErrors.errors.designation}
          >
            <input
              id="designation"
              name="designation"
              type="text"
              value={form.designation}
              onChange={handleChange}
            />
          </Field>
          <Field label="Department" htmlFor="department">
            <input
              id="department"
              name="department"
              type="text"
              value={form.department}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          {/* Experience is entered as two dates and never as a number of
              years. A number is right on the day it is typed and wrong every
              day after, and nobody comes back to revise it; a date stays true
              and the years are worked out on every read. The two are separate
              because they answer different questions — a tender asks for
              career experience and for length of service with the bidder, and
              answering one with the other rules out people who qualify. */}
          <Field
            label="Total Professional Experience"
            htmlFor="career_start_date"
            hint={`Date their professional career began, at any employer.${experienceHint(
              form.career_start_date,
            )}`}
          >
            <input
              id="career_start_date"
              name="career_start_date"
              type="date"
              value={form.career_start_date}
              onChange={handleChange}
            />
          </Field>
          <Field
            label="Experience with VGIL"
            htmlFor="date_of_joining"
            hint={`Date they joined VGIL.${experienceHint(form.date_of_joining)}`}
          >
            <input
              id="date_of_joining"
              name="date_of_joining"
              type="date"
              value={form.date_of_joining}
              onChange={handleChange}
            />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Highest qualification" htmlFor="highest_qualification">
            <input
              id="highest_qualification"
              name="highest_qualification"
              type="text"
              value={form.highest_qualification}
              onChange={handleChange}
              placeholder="e.g. B.E. Computer Science"
            />
          </Field>
          <Field label="Email" htmlFor="email">
            <input id="email" name="email" type="text" value={form.email} onChange={handleChange} />
          </Field>
        </FormRow>

        <FormRow>
          <Field label="Phone" htmlFor="phone">
            <input id="phone" name="phone" type="text" value={form.phone} onChange={handleChange} />
          </Field>
          <Field label="Key skills" htmlFor="key_skills">
            <input
              id="key_skills"
              name="key_skills"
              type="text"
              value={form.key_skills}
              onChange={handleChange}
              placeholder="Comma separated"
            />
          </Field>
        </FormRow>

        <Field label="Notes" htmlFor="notes">
          <textarea id="notes" name="notes" rows={3} value={form.notes} onChange={handleChange} />
        </Field>

        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? "Saving..." : isEditing ? "Save changes" : "Create employee"}
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
              Delete employee
            </button>
          )}
        </div>
      </form>

      {confirmingLeave && (
        <ConfirmDiscard
          body="This employee has changes that have not been saved. Leaving now loses them."
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
          title="Delete this employee?"
          confirmLabel="Delete employee"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">{form.full_name || "Unnamed employee"}</p>
          <p className="muted">
            The employee record will be permanently removed, along with {cvCount}{" "}
            {cvCount === 1 ? "CV entry" : "CV entries"} and {certificationCount}{" "}
            {certificationCount === 1 ? "certification" : "certifications"}. Files already
            uploaded to the server are not deleted.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

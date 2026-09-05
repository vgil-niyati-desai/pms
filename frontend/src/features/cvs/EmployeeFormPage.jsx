import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import ConfirmDelete from "../../components/ConfirmDelete";
import useResource from "../../hooks/useResource";
import {
  EMPTY_EMPLOYEE,
  createEmployee,
  deleteEmployee,
  getEmployee,
  updateEmployee,
} from "../../api/employees";
import { paths } from "../../app/routes";

/** Pulls just the editable fields off a record, so ids and timestamps stay put. */
function formFromEmployee(employee) {
  const next = { ...EMPTY_EMPLOYEE };
  for (const key of Object.keys(EMPTY_EMPLOYEE)) {
    next[key] = employee[key] ?? EMPTY_EMPLOYEE[key];
  }
  return next;
}

export default function EmployeeFormPage() {
  const { employeeId } = useParams();
  const navigate = useNavigate();
  const isEditing = Boolean(employeeId);

  const [form, setForm] = useState(EMPTY_EMPLOYEE);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const loadEmployee = useCallback(
    () => (isEditing ? getEmployee(employeeId) : Promise.resolve(null)),
    [isEditing, employeeId],
  );
  const existing = useResource(loadEmployee);

  useEffect(() => {
    if (existing.data) setForm(formFromEmployee(existing.data));
  }, [existing.data]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!form.full_name.trim() || !form.designation.trim()) {
      setError("Please fill in Full name and Designation.");
      return;
    }
    if (form.experience_years !== "" && Number(form.experience_years) < 0) {
      setError("Total experience cannot be negative.");
      return;
    }

    setSubmitting(true);
    try {
      const saved = isEditing
        ? await updateEmployee(employeeId, form)
        : await createEmployee(form);
      navigate(paths.employee(saved.id), { replace: true });
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
      navigate(paths.cvs(), { replace: true });
    } catch (err) {
      setDeleteError(err.message || "Could not delete this employee.");
      setDeleting(false);
    }
  }

  // Only an edit has something to wait for; the create form is ready at once.
  if (isEditing && existing.loading) return <p className="muted">Loading...</p>;
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

        {error && <div className="alert alert-error">{error}</div>}

        <FormRow>
          <Field label="Full name *" htmlFor="full_name">
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
          <Field label="Designation *" htmlFor="designation">
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
          <Field label="Date of joining" htmlFor="date_of_joining">
            <input
              id="date_of_joining"
              name="date_of_joining"
              type="date"
              value={form.date_of_joining}
              onChange={handleChange}
            />
          </Field>
          <Field label="Total experience (years)" htmlFor="experience_years">
            <input
              id="experience_years"
              name="experience_years"
              type="number"
              min="0"
              step="0.5"
              value={form.experience_years}
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
          <Link className="btn" to={isEditing ? paths.employee(employeeId) : paths.cvs()}>
            Cancel
          </Link>
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

import { useRef, useState } from "react";
import Drawer from "../../components/Drawer";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";
import DocumentFileSection from "../../components/DocumentFileSection";
import ConfirmDelete from "../../components/ConfirmDelete";
import { addCv, updateCv, deleteCv, EMPTY_CV } from "../../api/employees";
import {
  CV_DOCUMENT_TYPE,
  createAttachment,
  changedDetails,
  deleteAttachment,
  updateAttachment,
} from "../../api/attachments";
import { cvLabel } from "./employeeFields";

function formFromCv(cv) {
  const next = {};
  for (const key of Object.keys(EMPTY_CV)) next[key] = cv[key] ?? "";
  return next;
}

/** Upload or edit one version of an employee's CV. */
export default function CvDrawer({ employee, cv, onClose, onSaved }) {
  const isEditing = Boolean(cv);

  const [form, setForm] = useState(() =>
    cv ? formFromCv(cv) : { version_label: "", purpose: "", cv_date: "", uploaded_by: "" },
  );
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const fileInputRef = useRef(null);
  // The form as it opened, to tell what an edit actually changed.
  const openedWith = useRef(form);
  // Saving and deleting are each two requests, and the second can fail on
  // its own. These remember that the first already succeeded, so a retry
  // finishes the job rather than starting it again -- as the attached
  // document drawer does.
  const createdIdRef = useRef(null);
  const deletedRef = useRef(false);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  // The details this drawer's own form decides. Everything else on the
  // document is written once, when it is created, and never again from here.
  function attachmentEdits(values) {
    return {
      title: values.version_label || "CV",
      date: values.cv_date,
      uploadedBy: values.uploaded_by,
    };
  }

  function attachmentDetails() {
    return {
      documentType: CV_DOCUMENT_TYPE,
      subjectName: employee.full_name,
      reference: employee.employee_code,
      ...attachmentEdits(form),
      file,
    };
  }

  // Only what changed since the drawer opened, so an edit never rewrites
  // document fields the person did not touch here.
  function editedDetails() {
    return { ...changedDetails(attachmentEdits(openedWith.current), attachmentEdits(form)), file };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!isEditing && !file) {
      setError("Choose a CV file to upload.");
      return;
    }

    setSubmitting(true);
    try {
      // The file goes to the documents endpoint first; only once it is stored
      // does the local record get a pointer to it.
      let attachment = { document_id: cv?.document_id ?? null, file_name: cv?.file_name ?? null };
      if (createdIdRef.current) {
        // Uploaded on an earlier attempt; only saving the record failed.
        // Uploading again would orphan that first copy in the document log --
        // or be refused as a duplicate file -- so the retry reuses it.
        attachment = await updateAttachment(createdIdRef.current, attachmentDetails());
      } else if (!isEditing) {
        attachment = await createAttachment(attachmentDetails());
        createdIdRef.current = attachment.document_id;
      } else if (cv.document_id) {
        attachment = (await updateAttachment(cv.document_id, editedDetails())) ?? attachment;
      } else if (file) {
        attachment = await createAttachment(attachmentDetails());
        createdIdRef.current = attachment.document_id;
      }

      const values = { ...form, ...attachment };
      if (isEditing) await updateCv(employee.id, cv.id, values);
      else await addCv(employee.id, values);
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this CV.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      // Once the file is gone a retry must not delete it again: it would 404
      // and never reach the record itself.
      if (cv.document_id && !deletedRef.current) {
        await deleteAttachment(cv.document_id);
        deletedRef.current = true;
      }
      await deleteCv(employee.id, cv.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this CV.");
      setDeleting(false);
    }
  }

  return (
    <>
      <Drawer
        title={isEditing ? "Edit CV" : "Upload CV"}
        onClose={onClose}
        dismissable={!submitting && !deleting}
      >
        <form onSubmit={handleSubmit}>
          {error && <div className="alert alert-error">{error}</div>}

          <p className="muted hint drawer-context">
            {employee.full_name} &middot; {employee.designation}
          </p>

          <FormRow>
            <Field label="Version label" htmlFor="cv_version">
              <input
                id="cv_version"
                name="version_label"
                type="text"
                value={form.version_label}
                onChange={handleChange}
                placeholder="e.g. 2026 general"
              />
            </Field>
            <Field label="CV date" htmlFor="cv_date">
              <input
                id="cv_date"
                name="cv_date"
                type="date"
                value={form.cv_date}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <Field
            label="Purpose"
            htmlFor="cv_purpose"
            hint="What this version was tailored for, if anything."
          >
            <input
              id="cv_purpose"
              name="purpose"
              type="text"
              value={form.purpose}
              onChange={handleChange}
              placeholder="e.g. tailored for the MSAMB ERP tender"
            />
          </Field>

          <Field label="Uploaded by" htmlFor="cv_uploaded_by" hint="Optional.">
            <input
              id="cv_uploaded_by"
              name="uploaded_by"
              type="text"
              value={form.uploaded_by}
              onChange={handleChange}
              placeholder="Your name"
            />
          </Field>

          <FileUpload
            id="cv_file"
            name="cv_file"
            label={isEditing ? "Replace file (optional)" : "CV file (PDF, PNG, or JPG) *"}
            hint={
              isEditing
                ? cv.file_name
                  ? `Current file: ${cv.file_name}. Leave empty to keep it.`
                  : "No file attached yet."
                : null
            }
            onChange={(e) => setFile(e.target.files[0] || null)}
            ref={fileInputRef}
          />

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || deleting}>
              {submitting ? "Saving..." : isEditing ? "Save changes" : "Upload CV"}
            </button>
            <button
              type="button"
              className="btn"
              onClick={onClose}
              disabled={submitting || deleting}
            >
              Cancel
            </button>
            {isEditing && (
              <button
                type="button"
                className="btn btn-danger form-actions-end"
                onClick={() => setConfirmingDelete(true)}
                disabled={submitting || deleting}
              >
                Delete
              </button>
            )}
          </div>
        </form>

        {isEditing && (
          <DocumentFileSection documentId={cv.document_id} fileName={cv.file_name} />
        )}
      </Drawer>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this CV?"
          confirmLabel="Delete CV"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">
            {cvLabel(cv)} — {employee.full_name}
          </p>
          <p className="muted">
            {cv.file_name
              ? `This version and its uploaded file (${cv.file_name}) will be permanently removed.`
              : "This version will be permanently removed."}{" "}
            This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

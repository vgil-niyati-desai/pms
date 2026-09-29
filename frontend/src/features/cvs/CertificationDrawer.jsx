import { useRef, useState } from "react";
import Drawer from "../../components/Drawer";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";
import DocumentFileSection from "../../components/DocumentFileSection";
import ConfirmDelete from "../../components/ConfirmDelete";
import {
  EMPTY_CERTIFICATION,
  addCertification,
  updateCertification,
  deleteCertification,
} from "../../api/employees";
import {
  CERTIFICATION_DOCUMENT_TYPE,
  createAttachment,
  changedDetails,
  deleteAttachment,
  updateAttachment,
} from "../../api/attachments";

function formFromCertification(certification) {
  const next = {};
  for (const key of Object.keys(EMPTY_CERTIFICATION)) next[key] = certification[key] ?? "";
  return next;
}

const BLANK = {
  name: "",
  issuing_body: "",
  certificate_number: "",
  issue_date: "",
  expiry_date: "",
  notes: "",
};

/** Add or edit one of an employee's certifications. */
export default function CertificationDrawer({ employee, certification, onClose, onSaved }) {
  const isEditing = Boolean(certification);

  const [form, setForm] = useState(() =>
    certification ? formFromCertification(certification) : { ...BLANK },
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
      title: values.name,
      reference: values.certificate_number,
      date: values.issue_date,
    };
  }

  function attachmentDetails() {
    return {
      documentType: CERTIFICATION_DOCUMENT_TYPE,
      subjectName: employee.full_name,
      // No uploadedBy: this drawer does not ask who uploaded the file, and the
      // holder's name is not that -- so "Submitted by" is left empty rather
      // than filled with the wrong person.
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

    if (!form.name.trim()) {
      setError("Please fill in the Certificate name.");
      return;
    }
    if (form.issue_date && form.expiry_date && form.expiry_date < form.issue_date) {
      setError("Expiry date cannot be before the issue date.");
      return;
    }

    setSubmitting(true);
    try {
      // A certificate can be recorded without a scan; the file is only sent
      // when one is actually chosen.
      let attachment = {
        document_id: certification?.document_id ?? null,
        file_name: certification?.file_name ?? null,
      };
      if (createdIdRef.current) {
        // Uploaded on an earlier attempt; only saving the record failed.
        // Uploading again would orphan that first copy in the document log --
        // or be refused as a duplicate file -- so the retry reuses it.
        attachment = await updateAttachment(createdIdRef.current, attachmentDetails());
      } else if (file && attachment.document_id) {
        attachment = (await updateAttachment(attachment.document_id, editedDetails())) ?? attachment;
      } else if (file) {
        attachment = await createAttachment(attachmentDetails());
        createdIdRef.current = attachment.document_id;
      } else if (isEditing && attachment.document_id) {
        attachment = (await updateAttachment(attachment.document_id, editedDetails())) ?? attachment;
      }

      const values = { ...form, ...attachment };
      if (isEditing) await updateCertification(employee.id, certification.id, values);
      else await addCertification(employee.id, values);
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this certification.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      // Once the file is gone a retry must not delete it again: it would 404
      // and never reach the record itself.
      if (certification.document_id && !deletedRef.current) {
        await deleteAttachment(certification.document_id);
        deletedRef.current = true;
      }
      await deleteCertification(employee.id, certification.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this certification.");
      setDeleting(false);
    }
  }

  return (
    <>
      <Drawer
        title={isEditing ? "Edit certification" : "Add certification"}
        onClose={onClose}
        dismissable={!submitting && !deleting}
      >
        <form onSubmit={handleSubmit}>
          {error && <div className="alert alert-error">{error}</div>}

          <p className="muted hint drawer-context">
            {employee.full_name} &middot; {employee.designation}
          </p>

          <Field label="Certificate name *" htmlFor="cert_name">
            <input
              id="cert_name"
              name="name"
              type="text"
              value={form.name}
              onChange={handleChange}
              placeholder="e.g. ISO 27001 Lead Auditor"
            />
          </Field>

          <FormRow>
            <Field label="Issuing body" htmlFor="cert_issuer">
              <input
                id="cert_issuer"
                name="issuing_body"
                type="text"
                value={form.issuing_body}
                onChange={handleChange}
              />
            </Field>
            <Field label="Certificate number" htmlFor="cert_number">
              <input
                id="cert_number"
                name="certificate_number"
                type="text"
                value={form.certificate_number}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <FormRow>
            <Field label="Issue date" htmlFor="cert_issue_date">
              <input
                id="cert_issue_date"
                name="issue_date"
                type="date"
                value={form.issue_date}
                onChange={handleChange}
              />
            </Field>
            <Field
              label="Expiry date"
              htmlFor="cert_expiry_date"
              hint="Leave empty if it does not expire."
            >
              <input
                id="cert_expiry_date"
                name="expiry_date"
                type="date"
                value={form.expiry_date}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <FileUpload
            id="cert_file"
            name="cert_file"
            label={isEditing ? "Replace file (optional)" : "Certificate file (optional)"}
            hint={
              isEditing
                ? certification.file_name
                  ? `Current file: ${certification.file_name}. Leave empty to keep it.`
                  : "No file attached yet."
                : null
            }
            onChange={(e) => setFile(e.target.files[0] || null)}
            ref={fileInputRef}
          />

          <Field label="Notes" htmlFor="cert_notes">
            <textarea
              id="cert_notes"
              name="notes"
              rows={3}
              value={form.notes}
              onChange={handleChange}
            />
          </Field>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || deleting}>
              {submitting ? "Saving..." : isEditing ? "Save changes" : "Add certification"}
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

        {isEditing && certification.document_id && (
          <DocumentFileSection
            documentId={certification.document_id}
            fileName={certification.file_name}
            title={certification.name}
          />
        )}
      </Drawer>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this certification?"
          confirmLabel="Delete certification"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">
            {certification.name} — {employee.full_name}
          </p>
          <p className="muted">
            {certification.file_name
              ? `The record and its uploaded file (${certification.file_name}) will be permanently removed.`
              : "The record will be permanently removed."}{" "}
            This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

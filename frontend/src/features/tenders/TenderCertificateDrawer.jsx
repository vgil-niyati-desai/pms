import { useRef, useState } from "react";
import Drawer from "../../components/Drawer";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";
import DocumentFileSection from "../../components/DocumentFileSection";
import ConfirmDelete from "../../components/ConfirmDelete";
import {
  EMPTY_TENDER_CERTIFICATE,
  addCertificate,
  updateCertificate,
  deleteCertificate,
} from "../../api/tenders";
import {
  TENDER_CERTIFICATE_DOCUMENT_TYPE,
  createAttachment,
  changedDetails,
  deleteAttachment,
  updateAttachment,
} from "../../api/attachments";

function formFromCertificate(certificate) {
  const next = {};
  for (const key of Object.keys(EMPTY_TENDER_CERTIFICATE)) next[key] = certificate[key] ?? "";
  return next;
}

const BLANK = {
  name: "",
  issuing_body: "",
  certificate_number: "",
  valid_from: "",
  valid_to: "",
  notes: "",
};

/**
 * A certificate obtained specifically for this tender — a bank guarantee, a
 * solvency certificate, and the like. Distinct from an employee's
 * certifications, which belong to a person rather than to a bid.
 */
export default function TenderCertificateDrawer({ tender, certificate, onClose, onSaved }) {
  const isEditing = Boolean(certificate);

  const [form, setForm] = useState(() =>
    certificate ? formFromCertificate(certificate) : { ...BLANK },
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
      title: `${values.name} — ${tender.title}`,
      reference: values.certificate_number || tender.reference_number,
      date: values.valid_from,
    };
  }

  function attachmentDetails() {
    return {
      documentType: TENDER_CERTIFICATE_DOCUMENT_TYPE,
      subjectName: tender.issuing_authority,
      uploadedBy: tender.title,
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
    if (form.valid_from && form.valid_to && form.valid_to < form.valid_from) {
      setError("Valid to cannot be before valid from.");
      return;
    }

    setSubmitting(true);
    try {
      // The scan is optional; the file is only sent when one is chosen.
      let attachment = {
        document_id: certificate?.document_id ?? null,
        file_name: certificate?.file_name ?? null,
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
      if (isEditing) await updateCertificate(tender.id, certificate.id, values);
      else await addCertificate(tender.id, values);
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this certificate.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      // Once the file is gone a retry must not delete it again: it would 404
      // and never reach the record itself.
      if (certificate.document_id && !deletedRef.current) {
        await deleteAttachment(certificate.document_id);
        deletedRef.current = true;
      }
      await deleteCertificate(tender.id, certificate.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this certificate.");
      setDeleting(false);
    }
  }

  return (
    <>
      <Drawer
        title={isEditing ? "Edit submitted certificate" : "Add submitted certificate"}
        onClose={onClose}
        dismissable={!submitting && !deleting}
      >
        <form onSubmit={handleSubmit}>
          {error && <div className="alert alert-error">{error}</div>}

          <p className="muted hint drawer-context">
            {tender.title} &middot; {tender.issuing_authority}
          </p>

          <Field label="Certificate name *" htmlFor="tcert_name">
            <input
              id="tcert_name"
              name="name"
              type="text"
              value={form.name}
              onChange={handleChange}
              placeholder="e.g. Bank guarantee, Solvency certificate"
            />
          </Field>

          <FormRow>
            <Field label="Issuing body" htmlFor="tcert_issuer">
              <input
                id="tcert_issuer"
                name="issuing_body"
                type="text"
                value={form.issuing_body}
                onChange={handleChange}
              />
            </Field>
            <Field label="Certificate number" htmlFor="tcert_number">
              <input
                id="tcert_number"
                name="certificate_number"
                type="text"
                value={form.certificate_number}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <FormRow>
            <Field label="Valid from" htmlFor="tcert_valid_from">
              <input
                id="tcert_valid_from"
                name="valid_from"
                type="date"
                value={form.valid_from}
                onChange={handleChange}
              />
            </Field>
            <Field
              label="Valid to"
              htmlFor="tcert_valid_to"
              hint="Leave empty if it does not expire."
            >
              <input
                id="tcert_valid_to"
                name="valid_to"
                type="date"
                value={form.valid_to}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <FileUpload
            id="tcert_file"
            name="tcert_file"
            label={isEditing ? "Replace file (optional)" : "Certificate file (optional)"}
            hint={
              isEditing
                ? certificate.file_name
                  ? `Current file: ${certificate.file_name}. Leave empty to keep it.`
                  : "No file attached yet."
                : null
            }
            onChange={(e) => setFile(e.target.files[0] || null)}
            ref={fileInputRef}
          />

          <Field label="Notes" htmlFor="tcert_notes">
            <textarea
              id="tcert_notes"
              name="notes"
              rows={3}
              value={form.notes}
              onChange={handleChange}
            />
          </Field>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || deleting}>
              {submitting ? "Saving..." : isEditing ? "Save changes" : "Add submitted certificate"}
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

        {isEditing && certificate.document_id && (
          <DocumentFileSection
            documentId={certificate.document_id}
            fileName={certificate.file_name}
            title={certificate.name}
          />
        )}
      </Drawer>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this certificate?"
          confirmLabel="Delete certificate"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">
            {certificate.name} — {tender.title}
          </p>
          <p className="muted">
            {certificate.file_name
              ? `The record and its uploaded file (${certificate.file_name}) will be permanently removed.`
              : "The record will be permanently removed."}{" "}
            This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

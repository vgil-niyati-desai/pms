import { useRef, useState } from "react";
import Drawer from "../../components/Drawer";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";
import DocumentFileSection from "../../components/DocumentFileSection";
import ConfirmDelete from "../../components/ConfirmDelete";
import { createDocument, deleteDocument, updateDocument } from "../../api/documents";

const EMPTY_FORM = {
  document_type: "",
  category: "",
  reference_number: "",
  document_date: "",
  submitted_by: "",
  notes: "",
};

function formFromDocument(document) {
  const next = { ...EMPTY_FORM };
  for (const key of Object.keys(EMPTY_FORM)) next[key] = document[key] ?? "";
  return next;
}

/**
 * Add or edit a document attached to some owning record — a project or a
 * tender.
 *
 * The owner supplies the fields the document endpoint requires but that would
 * be tedious to retype (who it is for, what it belongs to), so they are sent
 * with the document rather than asked for again. That keeps each record
 * standing on its own in the document log.
 *
 * The owner also owns the link itself: `onLink` and `onUnlink` are what record
 * the association, since that lives in the owner's store until documents carry
 * an owner id of their own.
 */
export default function AttachedDocumentDrawer({
  context,
  documentTypes,
  document,
  presetType,
  onLink,
  onUnlink,
  onClose,
  onSaved,
}) {
  const isEditing = Boolean(document);

  const [form, setForm] = useState(() =>
    document ? formFromDocument(document) : { ...EMPTY_FORM, document_type: presetType || "" },
  );
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const fileInputRef = useRef(null);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function buildFormData() {
    const data = new FormData();
    Object.entries(form).forEach(([key, value]) => data.append(key, value));
    // Carried from the owning record so the document reads correctly alone.
    data.append("client_name", context.subjectName);
    data.append("project_title", context.title);
    data.append("contract_value", context.contractValue || "");
    data.append("department", context.department || "");
    // Omitting the file on an edit is what tells the backend to keep the
    // existing one.
    if (file) data.append("document_file", file);
    return data;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!form.document_type || !form.category.trim() || !form.submitted_by.trim()) {
      setError("Please fill in Document type, Category, and Added by.");
      return;
    }

    setSubmitting(true);
    try {
      if (isEditing) {
        await updateDocument(document.id, buildFormData());
      } else {
        const saved = await createDocument(buildFormData());
        await onLink(saved.id);
      }
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this document.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteDocument(document.id);
      await onUnlink(document.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this document.");
      setDeleting(false);
    }
  }

  return (
    <>
      <Drawer
        title={isEditing ? "Edit document" : "Add document"}
        onClose={onClose}
        dismissable={!submitting && !deleting}
      >
        <form onSubmit={handleSubmit}>
          {error && <div className="alert alert-error">{error}</div>}

          <p className="muted hint drawer-context">
            {context.title} &middot; {context.subjectName}
          </p>

          <FormRow>
            <Field label="Document type *" htmlFor="doc_type">
              <select
                id="doc_type"
                name="document_type"
                value={form.document_type}
                onChange={handleChange}
              >
                <option value="">Select type</option>
                {documentTypes.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Document date" htmlFor="doc_date">
              <input
                id="doc_date"
                name="document_date"
                type="date"
                value={form.document_date}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <Field label="Category *" htmlFor="doc_category">
            <input
              id="doc_category"
              name="category"
              type="text"
              value={form.category}
              onChange={handleChange}
              placeholder="e.g. ERP Implementation & Operational Experience"
            />
          </Field>

          <FormRow>
            <Field label="Reference number" htmlFor="doc_reference">
              <input
                id="doc_reference"
                name="reference_number"
                type="text"
                value={form.reference_number}
                onChange={handleChange}
              />
            </Field>
            <Field label="Added by *" htmlFor="doc_submitted_by">
              <input
                id="doc_submitted_by"
                name="submitted_by"
                type="text"
                value={form.submitted_by}
                onChange={handleChange}
                placeholder="Your name"
              />
            </Field>
          </FormRow>

          <FileUpload
            id="doc_file"
            name="document_file"
            label={isEditing ? "Replace file (optional)" : "Upload file (PDF, PNG, or JPG)"}
            hint={
              isEditing
                ? document.file_name
                  ? `Current file: ${document.file_name}. Leave empty to keep it.`
                  : "No file attached yet."
                : null
            }
            onChange={(e) => setFile(e.target.files[0] || null)}
            ref={fileInputRef}
          />

          <Field label="Notes" htmlFor="doc_notes">
            <textarea
              id="doc_notes"
              name="notes"
              rows={3}
              value={form.notes}
              onChange={handleChange}
            />
          </Field>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || deleting}>
              {submitting ? "Saving..." : isEditing ? "Save changes" : "Add document"}
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
          <DocumentFileSection
            documentId={document.file_name ? document.id : null}
            fileName={document.file_name}
            title={document.document_type}
          />
        )}
      </Drawer>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this document?"
          confirmLabel="Delete document"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">
            {document.document_type}
            {document.reference_number ? ` — ${document.reference_number}` : ""}
          </p>
          <p className="muted">
            {document.file_name
              ? `The record and its uploaded file (${document.file_name}) will be permanently removed.`
              : "The record will be permanently removed."}{" "}
            This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

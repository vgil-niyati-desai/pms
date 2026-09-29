import { useRef, useState } from "react";
import Drawer from "../../components/Drawer";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";
import DocumentFileSection from "../../components/DocumentFileSection";
import ConfirmDelete from "../../components/ConfirmDelete";
import SegmentedControl from "../../components/SegmentedControl";
import AttachExistingDocument from "./AttachExistingDocument";
import {
  createDocument,
  deleteDocument,
  updateDocument,
  updateDocumentFields,
} from "../../api/documents";

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
  attachedIds,
  onLink,
  onUnlink,
  onClose,
  onSaved,
}) {
  const isEditing = Boolean(document);

  // Attaching an existing document needs an owner that documents can
  // belong to, which today means a project. An owner that supplies no
  // projectId -- a tender -- gets the upload form on its own, exactly as
  // before.
  // A project claims what it attaches; a tender (context.tenderId) links
  // to it and leaves it where it is.
  const canAttachExisting = !isEditing && Boolean(context.projectId || context.tenderId);
  // A document this tender lists but does not hold -- a project's, a
  // person's, another bid's. Removing it from here unlinks it; deleting it
  // would delete it from where it belongs.
  const linkedOnly =
    isEditing && Boolean(context.tenderId) && document.tender_id !== context.tenderId;
  const [mode, setMode] = useState("upload");
  const attaching = canAttachExisting && mode === "attach";

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
  // Saving and deleting are each two requests, and the second can fail on
  // its own. These remember that the first already succeeded, so a retry
  // finishes the job rather than starting it again.
  const createdIdRef = useRef(null);
  const deletedRef = useRef(false);

  // A document already in the log can carry a type this list does not offer --
  // imported entries do, e.g. "Agreement" or "Acceptance of Tender-cum-Order".
  // Without its own option the select renders blank, so the type reads as
  // unset and any other edit to the form looks like it cleared it.
  const typeOptions =
    form.document_type && !documentTypes.includes(form.document_type)
      ? [...documentTypes, form.document_type]
      : documentTypes;

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function buildFormData() {
    const data = new FormData();
    Object.entries(form).forEach(([key, value]) => data.append(key, value));
    // Carried from the owning record so the document reads correctly alone.
    // Guarded like the two below it: client_name is optional on a project, and
    // FormData would turn a missing one into the literal string "undefined".
    data.append("client_name", context.subjectName || "");
    data.append("project_title", context.title || "");
    data.append("contract_value", context.contractValue || "");
    data.append("department", context.department || "");
    // Only the project screen supplies this. Sending it makes the create
    // attach the document in the same request; owners that are not projects
    // -- a tender -- send nothing and the document belongs to none.
    if (context.projectId) data.append("project_id", context.projectId);
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
        // Only the fields changed in this form. The owner's details --
        // client, title, value, department -- were stamped when the document
        // was created; rewriting them on every edit overwrote a document that
        // also belongs to, or was attached from, somewhere else.
        const opened = formFromDocument(document);
        const changes = {};
        for (const key of Object.keys(form)) {
          if (form[key] !== opened[key]) changes[key] = form[key];
        }
        if (file || Object.keys(changes).length > 0) {
          await updateDocumentFields(document.id, changes, file);
        }
      } else if (createdIdRef.current) {
        // Created on an earlier attempt; only the link failed. Creating it
        // again would leave that first copy orphaned in the document log --
        // or be refused as a duplicate file -- so the retry saves any edits
        // onto that record and links it.
        await updateDocument(createdIdRef.current, buildFormData());
        await onLink(createdIdRef.current);
      } else {
        const saved = await createDocument(buildFormData());
        createdIdRef.current = saved.id;
        await onLink(saved.id);
      }
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this document.");
      setSubmitting(false);
    }
  }

  // The same link call the upload path makes after creating a record --
  // here it is the whole operation, because the record already exists.
  async function handleAttachExisting(documentId) {
    await onLink(documentId);
    onSaved();
  }

  async function handleRemoveLink() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await onUnlink(document.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not remove this document from the tender.");
      setDeleting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      if (!deletedRef.current) {
        await deleteDocument(document.id);
        deletedRef.current = true;
      }
      // Detaching is the second call. If only that failed, a retry must not
      // delete again: the record is already gone, so it would 404 and leave
      // the project still pointing at it.
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
        {canAttachExisting && (
          <SegmentedControl
            label="How to add a document"
            options={[
              { key: "upload", label: "Upload new" },
              { key: "attach", label: "Attach existing" },
            ]}
            value={mode}
            onChange={setMode}
          />
        )}

        {attaching ? (
          <AttachExistingDocument
            projectId={context.projectId}
            tenderId={context.tenderId}
            attachedIds={attachedIds}
            onAttach={handleAttachExisting}
            onCancel={onClose}
          />
        ) : (
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
                  {typeOptions.map((type) => (
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
                  {linkedOnly ? "Remove from this tender" : "Delete document"}
                </button>
              )}
            </div>
          </form>
        )}

        {isEditing && (
          <DocumentFileSection
            documentId={document.file_name ? document.id : null}
            fileName={document.file_name}
            title={document.document_type}
          />
        )}
      </Drawer>

      {confirmingDelete && linkedOnly && (
        <ConfirmDelete
          title="Remove this document from the tender?"
          confirmLabel="Remove from tender"
          busyLabel="Removing..."
          busy={deleting}
          error={deleteError}
          onConfirm={handleRemoveLink}
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
            The tender stops listing it. The document itself, its file and wherever else it
            belongs are not changed.
          </p>
        </ConfirmDelete>
      )}

      {confirmingDelete && !linkedOnly && (
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

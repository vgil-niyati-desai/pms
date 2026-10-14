import { useState, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import {
  createDocument,
  listDocumentCategories,
  listDocumentTypes,
  updateDocument,
} from "../../api/documents";
import Field from "../../components/Field";
import useFieldErrors from "../../hooks/useFieldErrors";
import FormRow from "../../components/FormRow";
import FileUpload, { fileProblem } from "../../components/FileUpload";
import useRememberedAddedBy from "../../hooks/useRememberedAddedBy";
import useResource from "../../hooks/useResource";
import useUnsavedChanges from "../../hooks/useUnsavedChanges";
import { useRequestDrawerClose } from "../../components/Drawer";
import { formatFileSize } from "../../lib/format";
import { documentTypeOptions } from "./documentTypes";

const EMPTY_FORM = {
  document_type: "",
  category: "",
  client_name: "",
  reference_number: "",
  project_title: "",
  contract_value: "",
  document_date: "",
  department: "",
  submitted_by: "",
  notes: "",
};

// Pulls just the editable fields off a record, turning nulls into "" so the
// inputs stay controlled.
function formFromDocument(doc) {
  const next = { ...EMPTY_FORM };
  Object.keys(EMPTY_FORM).forEach((key) => {
    next[key] = doc[key] ?? "";
  });
  return next;
}

export default function DocumentForm({ onSaved, editingDocument, onCancelEdit, onDirtyChange }) {
  // Whoever filed the last entry.
  const [rememberedAddedBy, rememberAddedBy] = useRememberedAddedBy();
  // What a new entry starts from. Used everywhere EMPTY_FORM stood for "a new
  // entry" -- the first render, the baseline unsaved changes are measured
  // against, the reset after a save -- so the name reads as a default the form
  // came with and not as something somebody typed and might lose.
  const blankForm = { ...EMPTY_FORM, submitted_by: rememberedAddedBy };
  const [form, setForm] = useState(blankForm);
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const fieldErrors = useFieldErrors();
  const [success, setSuccess] = useState(null);
  // Name of the file currently attached to the entry being edited. Tracked
  // separately so it stays accurate after a replace without re-prefilling
  // the whole form.
  const [attachedFileName, setAttachedFileName] = useState(null);
  const fileInputRef = useRef(null);
  const formRef = useRef(null);

  const isEditing = Boolean(editingDocument);

  // Every type the log holds, and always the entry's own: a CV, a receipt or
  // an imported entry used to open with a blank type, because the picker only
  // offered the five project evidence types.
  const loadTypes = useCallback(() => listDocumentTypes(), []);
  const types = useResource(loadTypes, { initialData: [] });

  // Suggestions only: the field still accepts whatever is typed.
  const loadCategories = useCallback(() => listDocumentCategories(), []);
  const categories = useResource(loadCategories, { initialData: [] });
  const typeOptions = documentTypeOptions(types.data, form.document_type);
  const editingId = editingDocument ? editingDocument.id : null;

  // What closing the drawer would lose. The baseline is derived from the entry
  // rather than captured, because the effect below loads it a render late --
  // a captured one would be the blank form and the loaded entry would read as
  // somebody's change. The drawer owns the close routes, so the answer is
  // reported up to it.
  const { dirty } = useUnsavedChanges(
    { ...form, _file: file ? file.name : "" },
    { ...(editingDocument ? formFromDocument(editingDocument) : blankForm), _file: "" },
  );

  // A layout effect, as the redactor reports its own: Escape pressed straight
  // after a keystroke must already find the form unsaved.
  useLayoutEffect(() => {
    if (onDirtyChange) onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  // Cancel goes through the drawer's guarded close where there is one, so it
  // asks about unsaved changes like the backdrop and Escape do.
  const cancel = useRequestDrawerClose() ?? onCancelEdit;

  // Load the selected entry into the form (or reset it when editing stops).
  useEffect(() => {
    setForm(editingDocument ? formFromDocument(editingDocument) : blankForm);
    setAttachedFileName(editingDocument ? editingDocument.file_name ?? null : null);
    setFile(null);
    setError(null);
    fieldErrors.reset();
    setSuccess(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (editingDocument && formRef.current) {
      formRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [editingDocument]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    fieldErrors.clear(name);
  }

  function handleFileChange(e) {
    const picked = e.target.files[0] || null;
    const problem = fileProblem(picked);
    if (problem) {
      fieldErrors.set("document_file", problem);
      e.target.value = "";
      setFile(null);
      return;
    }
    // A fresh, valid pick is itself a correction, so any file error
    // from a moment ago no longer applies.
    fieldErrors.clear("document_file");
    setFile(picked);
  }

  // `addedBy` is what the emptied form keeps in that one field. It is passed
  // in by the save that has just remembered a name, because this runs in the
  // same handler and the state behind the default is a render behind.
  function resetForm(addedBy = rememberedAddedBy) {
    setForm({ ...EMPTY_FORM, submitted_by: addedBy });
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    const problems = {};
    if (!form.document_type) problems.document_type = "Please choose a Document type.";
    if (!form.category) problems.category = "Please fill in Category.";
    if (!form.client_name) problems.client_name = "Please fill in Client name.";
    if (!form.submitted_by) problems.submitted_by = "Please fill in Added by.";
    if (fieldErrors.report(problems)) return;

    setSubmitting(true);
    try {
      const data = new FormData();
      Object.entries(form).forEach(([key, value]) => data.append(key, value));
      // Only send a file when one was picked. On edit, leaving this out is
      // what tells the backend to keep the existing file.
      if (file) data.append("document_file", file);

      if (isEditing) {
        const saved = await updateDocument(editingId, data);
        setAttachedFileName(saved.file_name ?? null);
        // Clear only the file picker — the metadata stays on screen so the
        // entry can be adjusted again without re-opening it.
        setFile(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
        setSuccess("Entry updated successfully.");
      } else {
        await createDocument(data);
        // Only a new entry: see the hook for why an edit does not feed this.
        const addedBy = rememberAddedBy(form.submitted_by);
        setSuccess("Entry saved successfully.");
        resetForm(addedBy);
      }
      if (onSaved) onSaved();
    } catch (err) {
      setError(err.message || "Something went wrong while saving.");
    } finally {
      setSubmitting(false);
    }
  }

  // What picking a file actually did takes priority over what was there
  // before: once a replacement is chosen, the old file name is no longer
  // what is about to be saved, and restating it reads as though the pick
  // did not register.
  const fileHint = file
    ? `Selected: ${file.name} (${formatFileSize(file.size)})`
    : isEditing
      ? attachedFileName
        ? `Current file: ${attachedFileName}. Leave empty to keep it.`
        : "No file attached yet."
      : null;

  return (
    <form onSubmit={handleSubmit} ref={formRef}>
      {fieldErrors.summary && <div className="alert alert-error">{fieldErrors.summary}</div>}
      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      <FormRow>
        <Field
          label="Document type *"
          htmlFor="document_type"
          required
          error={fieldErrors.errors.document_type}
        >
          <select id="document_type" name="document_type" value={form.document_type} onChange={handleChange}>
            <option value="">Select type</option>
            {typeOptions.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </Field>
        {/* Still free text -- a new heading has to be possible to write --
            but the ones already in use are offered, so the same heading is
            not retyped three ways. The log's filter matches a category
            exactly, which is what makes a near-miss spelling cost something.
            A datalist, as the tag box and the criterion drawer's role field
            already use. */}
        <Field
          label="Category *"
          htmlFor="category"
          hint="Pick one already in use where it fits, or type a new one."
          required
          error={fieldErrors.errors.category}
        >
          <input
            id="category"
            name="category"
            type="text"
            list="document_category_suggestions"
            value={form.category}
            onChange={handleChange}
            placeholder="e.g. ERP Implementation & Operational Experience"
          />
          <datalist id="document_category_suggestions">
            {categories.data.map((category) => (
              <option key={category} value={category} />
            ))}
          </datalist>
        </Field>
      </FormRow>

      <FormRow>
        <Field
          label="Client / organization name *"
          htmlFor="client_name"
          required
          error={fieldErrors.errors.client_name}
        >
          <input id="client_name" name="client_name" type="text" value={form.client_name} onChange={handleChange} />
        </Field>
        <Field label="Reference number" htmlFor="reference_number">
          <input id="reference_number" name="reference_number" type="text" value={form.reference_number} onChange={handleChange} />
        </Field>
      </FormRow>

      <FormRow>
        <Field label="Project title" htmlFor="project_title">
          <input id="project_title" name="project_title" type="text" value={form.project_title} onChange={handleChange} />
        </Field>
        <Field label="Contract value" htmlFor="contract_value">
          <input id="contract_value" name="contract_value" type="text" value={form.contract_value} onChange={handleChange} placeholder="e.g. 5,00,000" />
        </Field>
      </FormRow>

      <FormRow>
        <Field label="Document date" htmlFor="document_date">
          <input id="document_date" name="document_date" type="date" value={form.document_date} onChange={handleChange} />
        </Field>
        <Field label="Department / team" htmlFor="department">
          <input id="department" name="department" type="text" value={form.department} onChange={handleChange} />
        </Field>
      </FormRow>

      <FormRow>
        <Field
          label="Added by *"
          htmlFor="submitted_by"
          required
          error={fieldErrors.errors.submitted_by}
        >
          <input id="submitted_by" name="submitted_by" type="text" value={form.submitted_by} onChange={handleChange} placeholder="Your name" />
        </Field>
        <FileUpload
          id="document_file"
          name="document_file"
          label={isEditing ? "Replace document (optional)" : "Upload document (PDF, PNG, or JPG)"}
          hint={fileHint}
          error={fieldErrors.errors.document_file}
          onChange={handleFileChange}
          ref={fileInputRef}
        />
      </FormRow>

      <Field label="Notes" htmlFor="notes">
        <textarea id="notes" name="notes" value={form.notes} onChange={handleChange} rows={3} />
      </Field>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting
            ? isEditing ? "Updating..." : "Saving..."
            : isEditing ? "Update entry" : "Save entry"}
        </button>
        <button type="button" className="btn" onClick={cancel} disabled={submitting}>
          Cancel
        </button>
      </div>
    </form>
  );
}

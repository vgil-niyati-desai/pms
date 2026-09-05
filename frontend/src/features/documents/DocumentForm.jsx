import { useState, useRef, useEffect } from "react";
import { createDocument, updateDocument } from "../../api/documents";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";

const DOCUMENT_TYPES = ["LOI", "Work Order", "Completion Certificate", "Purchase Order", "Contract"];

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

export default function DocumentForm({ onSaved, editingDocument, onCancelEdit }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  // Name of the file currently attached to the entry being edited. Tracked
  // separately so it stays accurate after a replace without re-prefilling
  // the whole form.
  const [attachedFileName, setAttachedFileName] = useState(null);
  const fileInputRef = useRef(null);
  const formRef = useRef(null);

  const isEditing = Boolean(editingDocument);
  const editingId = editingDocument ? editingDocument.id : null;

  // Load the selected entry into the form (or reset it when editing stops).
  useEffect(() => {
    setForm(editingDocument ? formFromDocument(editingDocument) : EMPTY_FORM);
    setAttachedFileName(editingDocument ? editingDocument.file_name ?? null : null);
    setFile(null);
    setError(null);
    setSuccess(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (editingDocument && formRef.current) {
      formRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [editingDocument]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function handleFileChange(e) {
    setFile(e.target.files[0] || null);
  }

  function resetForm() {
    setForm(EMPTY_FORM);
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!form.document_type || !form.category || !form.client_name || !form.submitted_by) {
      setError("Please fill in Document type, Category, Client name, and Submitted by.");
      return;
    }

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
        setSuccess("Entry saved successfully.");
        resetForm();
      }
      if (onSaved) onSaved();
    } catch (err) {
      setError(err.message || "Something went wrong while saving.");
    } finally {
      setSubmitting(false);
    }
  }

  const fileHint = isEditing
    ? attachedFileName
      ? `Current file: ${attachedFileName}. Leave empty to keep it.`
      : "No file attached yet."
    : null;

  return (
    <form className="card form" onSubmit={handleSubmit} ref={formRef}>
      <h2>{isEditing ? `Edit Entry #${editingId}` : "Add Entry"}</h2>

      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      <FormRow>
        <Field label="Document type *" htmlFor="document_type">
          <select id="document_type" name="document_type" value={form.document_type} onChange={handleChange}>
            <option value="">Select type</option>
            {DOCUMENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </Field>
        <Field label="Category *" htmlFor="category">
          <input
            id="category"
            name="category"
            type="text"
            value={form.category}
            onChange={handleChange}
            placeholder="e.g. ERP Implementation & Operational Experience"
          />
        </Field>
      </FormRow>

      <FormRow>
        <Field label="Client / organization name *" htmlFor="client_name">
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
        <Field label="Submitted by *" htmlFor="submitted_by">
          <input id="submitted_by" name="submitted_by" type="text" value={form.submitted_by} onChange={handleChange} placeholder="Your name" />
        </Field>
        <FileUpload
          id="document_file"
          name="document_file"
          label={isEditing ? "Replace document (optional)" : "Upload document (PDF, PNG, or JPG)"}
          hint={fileHint}
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
        {isEditing && (
          <button type="button" className="btn" onClick={onCancelEdit} disabled={submitting}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

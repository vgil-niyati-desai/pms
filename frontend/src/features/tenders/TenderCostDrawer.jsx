import { useRef, useState } from "react";
import Drawer from "../../components/Drawer";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import FileUpload from "../../components/FileUpload";
import DocumentFileSection from "../../components/DocumentFileSection";
import ConfirmDelete from "../../components/ConfirmDelete";
import {
  COST_TYPES,
  EMPTY_COST_ITEM,
  addCostItem,
  updateCostItem,
  deleteCostItem,
} from "../../api/tenders";
import {
  TENDER_RECEIPT_DOCUMENT_TYPE,
  createAttachment,
  changedDetails,
  deleteAttachment,
  updateAttachment,
} from "../../api/attachments";
import { formatAmount } from "../../lib/format";

const PAYMENT_MODES = ["DD", "NEFT / RTGS", "Bank guarantee", "Online portal", "Cash", "Other"];

function formFromCostItem(item) {
  const next = {};
  for (const key of Object.keys(EMPTY_COST_ITEM)) next[key] = item[key] ?? "";
  return next;
}

const BLANK = {
  cost_type: "",
  amount: "",
  payment_mode: "",
  instrument_number: "",
  paid_on: "",
};

/** Record or edit one thing this tender cost, with its receipt. */
export default function TenderCostDrawer({ tender, costItem, onClose, onSaved }) {
  const isEditing = Boolean(costItem);

  const [form, setForm] = useState(() =>
    costItem ? formFromCostItem(costItem) : { ...BLANK },
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
      title: `${values.cost_type} — ${tender.title}`,
      reference: values.instrument_number || tender.reference_number,
      date: values.paid_on,
    };
  }

  function attachmentDetails() {
    return {
      documentType: TENDER_RECEIPT_DOCUMENT_TYPE,
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

    if (!form.cost_type) {
      setError("Please choose a cost type.");
      return;
    }
    if (form.amount === "" || Number(form.amount) < 0) {
      setError("Please enter an amount of zero or more.");
      return;
    }

    setSubmitting(true);
    try {
      // A receipt is optional; the file is only sent when one is chosen.
      let attachment = {
        document_id: costItem?.document_id ?? null,
        file_name: costItem?.file_name ?? null,
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
      if (isEditing) await updateCostItem(tender.id, costItem.id, values);
      else await addCostItem(tender.id, values);
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this cost.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      // Once the file is gone a retry must not delete it again: it would 404
      // and never reach the record itself.
      if (costItem.document_id && !deletedRef.current) {
        await deleteAttachment(costItem.document_id);
        deletedRef.current = true;
      }
      await deleteCostItem(tender.id, costItem.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this cost.");
      setDeleting(false);
    }
  }

  return (
    <>
      <Drawer
        title={isEditing ? "Edit cost" : "Add cost"}
        onClose={onClose}
        dismissable={!submitting && !deleting}
      >
        <form onSubmit={handleSubmit}>
          {error && <div className="alert alert-error">{error}</div>}

          <p className="muted hint drawer-context">
            {tender.title} &middot; {tender.issuing_authority}
          </p>

          <FormRow>
            <Field label="Cost type *" htmlFor="cost_type">
              <select
                id="cost_type"
                name="cost_type"
                value={form.cost_type}
                onChange={handleChange}
              >
                <option value="">Select type</option>
                {COST_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Amount *" htmlFor="cost_amount" hint="Numbers only.">
              <input
                id="cost_amount"
                name="amount"
                type="number"
                min="0"
                value={form.amount}
                onChange={handleChange}
              />
            </Field>
          </FormRow>

          <FormRow>
            <Field label="Paid by" htmlFor="cost_payment_mode">
              <select
                id="cost_payment_mode"
                name="payment_mode"
                value={form.payment_mode}
                onChange={handleChange}
              >
                <option value="">Not set</option>
                {PAYMENT_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Instrument number" htmlFor="cost_instrument">
              <input
                id="cost_instrument"
                name="instrument_number"
                type="text"
                value={form.instrument_number}
                onChange={handleChange}
                placeholder="DD / UTR / transaction no."
              />
            </Field>
          </FormRow>

          <Field label="Paid on" htmlFor="cost_paid_on">
            <input
              id="cost_paid_on"
              name="paid_on"
              type="date"
              value={form.paid_on}
              onChange={handleChange}
            />
          </Field>

          <FileUpload
            id="cost_file"
            name="cost_file"
            label={isEditing ? "Replace receipt (optional)" : "Receipt (optional)"}
            hint={
              isEditing
                ? costItem.file_name
                  ? `Current file: ${costItem.file_name}. Leave empty to keep it.`
                  : "No receipt attached yet."
                : null
            }
            onChange={(e) => setFile(e.target.files[0] || null)}
            ref={fileInputRef}
          />

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || deleting}>
              {submitting ? "Saving..." : isEditing ? "Save changes" : "Add cost"}
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

        {isEditing && costItem.document_id && (
          <DocumentFileSection
            heading="Receipt"
            documentId={costItem.document_id}
            fileName={costItem.file_name}
            title={costItem.cost_type}
          />
        )}
      </Drawer>

      {confirmingDelete && (
        <ConfirmDelete
          title="Delete this cost?"
          confirmLabel="Delete cost"
          busy={deleting}
          error={deleteError}
          onConfirm={handleDelete}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
        >
          <p className="muted">
            {costItem.cost_type} — {formatAmount(costItem.amount)}
          </p>
          <p className="muted">
            {costItem.file_name
              ? `The record and its receipt (${costItem.file_name}) will be permanently removed.`
              : "The record will be permanently removed."}{" "}
            This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

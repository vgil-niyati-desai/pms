import { useCallback, useState } from "react";
import Drawer, { DrawerCancelButton } from "../../components/Drawer";
import Field from "../../components/Field";
import useFieldErrors from "../../hooks/useFieldErrors";
import FormRow from "../../components/FormRow";
import TagInput from "../../components/TagInput";
import ConfirmDelete from "../../components/ConfirmDelete";
import useResource from "../../hooks/useResource";
import useUnsavedChanges from "../../hooks/useUnsavedChanges";
import {
  COMPLIANCE_STATUSES,
  addCriterion,
  deleteCriterion,
  setCriterionCompliance,
  updateCriterion,
} from "../../api/tenders";
import { listCertificationNames, listDesignations } from "../../api/employees";
import { listDocumentTypes } from "../../api/documents";
import { formatAmount } from "../../lib/format";
import { documentTypeOptions } from "../documents/documentTypes";
import {
  COMPLIANCE_LABELS,
  COMPLIANCE_NEEDS_NOTE,
  CRITERION_KINDS,
  KIND_ORDER,
  complianceStatus,
  criterionSummary,
  kindLabel,
  paramsForm,
  paramsPayload,
  validateParams,
  visibleFields,
} from "./criteriaFields";

function frameFrom(criterion) {
  return {
    mandatory: criterion ? criterion.mandatory : true,
    clause_ref: criterion?.clause_ref ?? "",
    source_text: criterion?.source_text ?? "",
    notes: criterion?.notes ?? "",
  };
}

function complianceFrom(criterion) {
  return {
    status: complianceStatus(criterion),
    note: criterion?.compliance?.note ?? "",
    decided_by: criterion?.compliance?.decided_by ?? "",
  };
}

/**
 * Add or edit one qualification criterion.
 *
 * The type is chosen first and fixed once the criterion exists: its fields
 * were checked for that type, so a different type is a different criterion
 * -- delete this one and add the other. Every type shares the same frame
 * below its own fields: mandatory or desirable, where in the notice it came
 * from, and the wording as published.
 */
export default function CriterionDrawer({ tender, criterion, onClose, onSaved }) {
  const isEditing = Boolean(criterion);

  const [kind, setKind] = useState(criterion?.kind ?? "");
  const [params, setParams] = useState(() => (criterion ? paramsForm(criterion.kind, criterion.params) : {}));
  const [frame, setFrame] = useState(() => frameFrom(criterion));
  // Only offered on a criterion that exists: there is nothing to have checked
  // until the requirement has been recorded, and a new one starts unchecked.
  const [compliance, setCompliance] = useState(() => complianceFrom(criterion));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const fieldErrors = useFieldErrors();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  // What closing this drawer would lose, in one fixed key order. Kept
  // separate from requirementChanged/complianceChanged below: those decide
  // which call to send against the *stored* record, and treat a new criterion
  // as always changed so the POST is made. This asks a different question --
  // is there anything on screen that was not here when it opened -- so a
  // blank Add drawer is not dirty and does not ask on the way out.
  const { dirty } = useUnsavedChanges({ kind, params, frame, compliance });

  // Suggestions only: every one of these fields accepts whatever is typed.
  const loadDesignations = useCallback(() => listDesignations(), []);
  const designations = useResource(loadDesignations, { initialData: [] });
  const loadCertificationNames = useCallback(() => listCertificationNames(), []);
  const certificationNames = useResource(loadCertificationNames, { initialData: [] });
  const loadDocumentTypes = useCallback(() => listDocumentTypes(), []);
  const documentTypes = useResource(loadDocumentTypes, { initialData: [] });

  function chooseKind(next) {
    setKind(next);
    setParams(next ? paramsForm(next) : {});
    setError(null);
    // A different kind is a different set of fields; none of the old
    // messages apply to it.
    fieldErrors.reset();
  }

  function setParam(name, value) {
    setParams((prev) => ({ ...prev, [name]: value }));
    fieldErrors.clear(`criterion_${name}`);
  }

  function setFrameField(name, value) {
    setFrame((prev) => ({ ...prev, [name]: value }));
  }

  function setComplianceField(name, value) {
    setCompliance((prev) => ({ ...prev, [name]: value }));
    fieldErrors.clear("criterion_compliance_note");
  }

  // Compared as form state rather than as payloads, so the two sides are the
  // same shape and an unchanged criterion is recognised exactly. Both objects
  // are built key-for-key by the helpers above, so their order is stable.
  function requirementChanged() {
    if (!isEditing) return true;
    return (
      JSON.stringify(frame) !== JSON.stringify(frameFrom(criterion)) ||
      JSON.stringify(params) !== JSON.stringify(paramsForm(criterion.kind, criterion.params))
    );
  }

  function complianceChanged() {
    return isEditing && JSON.stringify(compliance) !== JSON.stringify(complianceFrom(criterion));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!kind) {
      fieldErrors.report({ criterion_kind: "Please choose the type of requirement." });
      return;
    }
    const problems = {};
    for (const [name, problem] of Object.entries(validateParams(kind, params))) {
      problems[`criterion_${name}`] = problem;
    }
    // Checked here as well as on the server, so the reason is asked for
    // before anything is sent rather than after the requirement has saved.
    if (COMPLIANCE_NEEDS_NOTE.includes(compliance.status) && !compliance.note.trim()) {
      problems.criterion_compliance_note = `Please say why this criterion is recorded as "${
        COMPLIANCE_LABELS[compliance.status]
      }".`;
    }
    if (fieldErrors.report(problems)) return;

    const payload = {
      kind,
      mandatory: frame.mandatory,
      clause_ref: frame.clause_ref.trim() || null,
      source_text: frame.source_text.trim() || null,
      notes: frame.notes.trim() || null,
      params: paramsPayload(kind, params),
    };

    setSubmitting(true);
    try {
      // Two records, two calls, and each only when it changed. The
      // requirement comes from the tender notice; whether we meet it is our
      // own judgement, and marking one must not rewrite the other.
      if (requirementChanged()) {
        if (isEditing) await updateCriterion(tender.id, criterion.id, payload);
        else await addCriterion(tender.id, payload);
      }
      if (complianceChanged()) {
        await setCriterionCompliance(tender.id, criterion.id, {
          status: compliance.status,
          note: compliance.note.trim() || null,
          decided_by: compliance.decided_by.trim() || null,
        });
      }
      onSaved();
    } catch (err) {
      setError(err.message || "Could not save this criterion.");
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteCriterion(tender.id, criterion.id);
      onSaved();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this criterion.");
      setDeleting(false);
    }
  }

  function renderField(field) {
    const id = `criterion_${field.name}`;
    const label = `${field.label}${field.required ? " *" : ""}`;
    const value = params[field.name];

    if (field.type === "checkbox") {
      return (
        <div className="field" key={field.name}>
          <label className="checkbox-row" htmlFor={id}>
            <input
              id={id}
              type="checkbox"
              checked={Boolean(value)}
              onChange={(e) => setParam(field.name, e.target.checked)}
            />
            {field.label}
          </label>
        </div>
      );
    }
    if (field.type === "list") {
      return (
        <TagInput
          key={field.name}
          id={id}
          label={field.label}
          error={fieldErrors.errors[id]}
          value={value}
          onChange={(next) => setParam(field.name, next)}
          suggestions={field.suggest === "certifications" ? certificationNames.data : []}
          hint="Press Enter or comma after each one."
        />
      );
    }

    let control;
    if (field.type === "select" || field.type === "documentType") {
      const options =
        field.type === "documentType"
          ? documentTypeOptions(documentTypes.data, value).map((type) => ({ value: type, label: type }))
          : field.options;
      control = (
        <select id={id} value={value} onChange={(e) => setParam(field.name, e.target.value)}>
          {field.type === "documentType" && <option value="">Select type</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    } else if (field.type === "textarea") {
      control = (
        <textarea id={id} rows={4} value={value} onChange={(e) => setParam(field.name, e.target.value)} />
      );
    } else {
      const numeric = ["int", "number", "amount"].includes(field.type);
      control = (
        <input
          id={id}
          type={numeric ? "number" : field.type === "date" ? "date" : "text"}
          // No native min or step: validateParams owns those rules, so the
          // message is the drawer's own, naming the field, not a browser bubble.
          step={numeric ? "any" : undefined}
          list={field.suggest === "designations" ? `${id}_suggestions` : undefined}
          value={value}
          onChange={(e) => setParam(field.name, e.target.value)}
        />
      );
    }

    // An amount reads back in rupees as it is typed, so a missing zero
    // is caught before it is saved.
    const hint =
      field.type === "amount" && String(value).trim() !== "" && Number.isFinite(Number(value))
        ? formatAmount(value)
        : field.hint;

    return (
      <Field
        key={field.name}
        label={label}
        htmlFor={id}
        hint={hint}
        required={field.required}
        error={fieldErrors.errors[id]}
      >
        {control}
        {field.suggest === "designations" && (
          <datalist id={`${id}_suggestions`}>
            {designations.data.map((designation) => (
              <option key={designation} value={designation} />
            ))}
          </datalist>
        )}
      </Field>
    );
  }

  const fields = kind ? visibleFields(kind, params) : [];

  return (
    <>
      <Drawer
        title={isEditing ? "Edit criterion" : "Add criterion"}
        onClose={onClose}
        dismissable={!submitting && !deleting}
        dirty={dirty}
      >
        <form onSubmit={handleSubmit}>
          {fieldErrors.summary && <div className="alert alert-error">{fieldErrors.summary}</div>}
          {error && <div className="alert alert-error">{error}</div>}

          <p className="muted hint drawer-context">
            {tender.title}
            {tender.issuing_authority ? <> &middot; {tender.issuing_authority}</> : null}
          </p>

          {isEditing ? (
            <Field label="Type" hint="Fixed once added. To record it as another type, delete it and add a new one.">
              <p className="prose">{kindLabel(kind)}</p>
            </Field>
          ) : (
            <Field
              label="Type *"
              htmlFor="criterion_kind"
              required
              error={fieldErrors.errors.criterion_kind}
            >
              <select id="criterion_kind" value={kind} onChange={(e) => chooseKind(e.target.value)}>
                <option value="">Select the type of requirement</option>
                {KIND_ORDER.map((key) => (
                  <option key={key} value={key}>
                    {CRITERION_KINDS[key].label}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {fields.map(renderField)}

          {kind && (
            <>
              <FormRow>
                <Field label="Requirement" htmlFor="criterion_mandatory">
                  <select
                    id="criterion_mandatory"
                    value={frame.mandatory ? "mandatory" : "desirable"}
                    onChange={(e) => setFrameField("mandatory", e.target.value === "mandatory")}
                  >
                    <option value="mandatory">Mandatory</option>
                    <option value="desirable">Desirable</option>
                  </select>
                </Field>
                <Field label="Clause reference" htmlFor="criterion_clause_ref">
                  <input
                    id="criterion_clause_ref"
                    type="text"
                    value={frame.clause_ref}
                    onChange={(e) => setFrameField("clause_ref", e.target.value)}
                    placeholder="e.g. Section 3.2 (a)"
                  />
                </Field>
              </FormRow>

              <Field
                label="Wording in the tender"
                htmlFor="criterion_source_text"
                hint="Paste the clause as published. It is what the fields above are checked against."
              >
                <textarea
                  id="criterion_source_text"
                  rows={4}
                  value={frame.source_text}
                  onChange={(e) => setFrameField("source_text", e.target.value)}
                />
              </Field>

              <Field label="Internal notes" htmlFor="criterion_notes">
                <textarea
                  id="criterion_notes"
                  rows={2}
                  value={frame.notes}
                  onChange={(e) => setFrameField("notes", e.target.value)}
                />
              </Field>

              {/* Only on a criterion that exists. Recorded here, not worked
                  out: Find candidates shows what is on file against this
                  requirement, and the call on whether it is met is made by
                  the person who read it. */}
              {isEditing && (
                <>
                  <h4>Compliance</h4>
                  <Field
                    label="Do we meet this requirement?"
                    htmlFor="criterion_compliance_status"
                    hint="Your own assessment. Nothing here is decided automatically."
                  >
                    <select
                      id="criterion_compliance_status"
                      value={compliance.status}
                      onChange={(e) => setComplianceField("status", e.target.value)}
                    >
                      {COMPLIANCE_STATUSES.map((status) => (
                        <option key={status} value={status}>
                          {COMPLIANCE_LABELS[status]}
                        </option>
                      ))}
                    </select>
                  </Field>

                  {/* Nothing to say about a criterion nobody has looked at,
                      and a reason left on one would read as a finding. */}
                  {compliance.status !== "not_checked" && (
                    <>
                      <Field
                        label={
                          COMPLIANCE_NEEDS_NOTE.includes(compliance.status)
                            ? "Reason *"
                            : "Reason"
                        }
                        htmlFor="criterion_compliance_note"
                        hint={
                          COMPLIANCE_NEEDS_NOTE.includes(compliance.status)
                            ? "Required: what is missing, or what could not be established. The next person reading this has only what you write here."
                            : "Optional: what satisfies it, e.g. which project or whose CV."
                        }
                        required={COMPLIANCE_NEEDS_NOTE.includes(compliance.status)}
                        error={fieldErrors.errors.criterion_compliance_note}
                      >
                        <textarea
                          id="criterion_compliance_note"
                          rows={3}
                          value={compliance.note}
                          onChange={(e) => setComplianceField("note", e.target.value)}
                        />
                      </Field>

                      <Field label="Assessed by" htmlFor="criterion_compliance_by">
                        <input
                          id="criterion_compliance_by"
                          type="text"
                          value={compliance.decided_by}
                          onChange={(e) => setComplianceField("decided_by", e.target.value)}
                          placeholder="Your name"
                        />
                      </Field>
                    </>
                  )}
                </>
              )}
            </>
          )}

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || deleting}>
              {submitting ? "Saving..." : isEditing ? "Save changes" : "Add criterion"}
            </button>
            {/* Asks first when something is unsaved, like every other way out. */}
            <DrawerCancelButton disabled={submitting || deleting} />
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
      </Drawer>

      {confirmingDelete && (
        <div className="drawer-confirm-layer">
          <ConfirmDelete
            title="Delete this criterion?"
            confirmLabel="Delete criterion"
            busy={deleting}
            error={deleteError}
            onConfirm={handleDelete}
            onCancel={() => {
              setConfirmingDelete(false);
              setDeleteError(null);
            }}
          >
            <p className="muted">{criterionSummary(criterion)}</p>
          </ConfirmDelete>
        </div>
      )}
    </>
  );
}

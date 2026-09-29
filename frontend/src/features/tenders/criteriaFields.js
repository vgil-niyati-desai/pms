import { formatAmount } from "../../lib/format";

/**
 * Qualification criteria: the kinds a requirement can be recorded as, the
 * fields each one asks for, and how each reads back as one line.
 *
 * The server validates every kind's params again (schemas.py); the checks
 * here are the same rules, run first so the drawer can say what is wrong
 * before anything is sent. Amounts are rupees, sent as numbers.
 */

/** The groups criteria are listed under, in display order. */
export const CRITERION_CATEGORIES = [
  { key: "technical", label: "Technical" },
  { key: "personnel", label: "Personnel" },
  { key: "financial", label: "Financial" },
  { key: "documents", label: "Documents" },
  { key: "other", label: "Other" },
];

const FINANCIAL_METRICS = [
  { value: "average_annual_turnover", label: "Average annual turnover" },
  { value: "net_worth", label: "Net worth" },
  { value: "solvency", label: "Solvency" },
  { value: "working_capital", label: "Working capital" },
  { value: "other", label: "Other (describe)" },
];

const VALUE_BASES = [
  { value: "single", label: "At least one project of this value" },
  { value: "total", label: "Total of the projects cited" },
  { value: "average", label: "Average of the projects cited" },
];

const EXPERIENCE_BASES = [
  { value: "total", label: "Total professional experience" },
  { value: "vgil", label: "Experience with VGIL" },
];

const MAX_YEARS = 60;

/**
 * Each field: `name` (the params key), `label`, `type` -- text, textarea,
 * int, number (years), amount (rupees), select, checkbox, list, date,
 * documentType -- plus `required`, `options`, `min`, `max` and `hint`.
 */
export const CRITERION_KINDS = {
  similar_projects: {
    label: "Similar projects",
    category: "technical",
    fields: [
      { name: "count", label: "Number of similar projects", type: "int", required: true, min: 1 },
      {
        name: "work_description",
        label: "Nature of the work",
        type: "text",
        hint: "What counts as similar, e.g. water supply pipelines.",
      },
      { name: "min_value_each", label: "Minimum value of each (₹)", type: "amount" },
      { name: "completed_within_years", label: "Within the last (years)", type: "number", min: 0, exclusiveMin: true, max: MAX_YEARS },
      { name: "must_be_completed", label: "Must be completed, not ongoing", type: "checkbox" },
    ],
  },
  project_value: {
    label: "Project value",
    category: "technical",
    fields: [
      { name: "min_value", label: "Minimum value (₹)", type: "amount", required: true, exclusiveMin: true },
      { name: "basis", label: "Measured as", type: "select", options: VALUE_BASES, required: true },
      { name: "within_years", label: "Within the last (years)", type: "number", min: 0, exclusiveMin: true, max: MAX_YEARS },
    ],
  },
  personnel: {
    label: "Personnel",
    category: "personnel",
    fields: [
      { name: "role", label: "Role / designation", type: "text", required: true, suggest: "designations" },
      { name: "count", label: "Number of people", type: "int", required: true, min: 1 },
      { name: "min_experience_years", label: "Minimum experience (years)", type: "number", min: 0, max: MAX_YEARS },
      { name: "experience_basis", label: "Experience counted as", type: "select", options: EXPERIENCE_BASES, required: true },
      { name: "qualification", label: "Qualification", type: "text", hint: "As written, e.g. B.E. Civil." },
      { name: "required_certifications", label: "Certifications held", type: "list", suggest: "certifications", max: 50 },
    ],
  },
  certification: {
    label: "Company certification",
    category: "documents",
    fields: [
      {
        name: "name",
        label: "Certification",
        type: "text",
        required: true,
        hint: "One the company must hold, e.g. ISO 9001 — not one submitted with this bid.",
      },
      { name: "issuing_body", label: "Issuing body", type: "text" },
      { name: "valid_on", label: "Must be valid on", type: "date", hint: "Leave empty for the submission date." },
    ],
  },
  document: {
    label: "Supporting document",
    category: "documents",
    fields: [
      { name: "document_type", label: "Document type", type: "documentType", required: true },
      { name: "count", label: "How many", type: "int", min: 1 },
      { name: "description", label: "Description", type: "text", hint: "e.g. for each similar work cited." },
      { name: "validity_note", label: "Validity", type: "text", hint: "e.g. issued within the last 6 months." },
    ],
  },
  financial: {
    label: "Financial",
    category: "financial",
    fields: [
      { name: "metric", label: "Requirement", type: "select", options: FINANCIAL_METRICS, required: true },
      { name: "description", label: "Describe the requirement", type: "text", showWhen: (form) => form.metric === "other", required: true },
      { name: "min_amount", label: "Minimum amount (₹)", type: "amount", required: true },
      { name: "period_years", label: "Over the last (financial years)", type: "int", min: 1, max: 20 },
    ],
  },
  other: {
    label: "Other requirement",
    category: "other",
    fields: [
      { name: "description", label: "Requirement", type: "textarea", required: true },
    ],
  },
};

/** Kinds in the order the type picker offers them. */
export const KIND_ORDER = [
  "similar_projects",
  "project_value",
  "personnel",
  "financial",
  "certification",
  "document",
  "other",
];

export function kindLabel(kind) {
  return CRITERION_KINDS[kind]?.label ?? kind;
}

export function priorityLabel(criterion) {
  return criterion.mandatory ? "Mandatory" : "Desirable";
}

// Kinds the candidates endpoint can say something about from recorded data,
// even if only that it cannot decide (project value). Financial and Other
// have nothing recorded to look at, so they are checked by hand.
const MATCHABLE_KINDS = ["similar_projects", "project_value", "personnel", "certification", "document"];

export function isMatchable(criterion) {
  return MATCHABLE_KINDS.includes(criterion.kind);
}

/** A field only shown for some values of another, like "describe" for Other. */
export function visibleFields(kind, form) {
  return (CRITERION_KINDS[kind]?.fields ?? []).filter((field) => !field.showWhen || field.showWhen(form));
}

// --- form <-> payload ----------------------------------------------------------
//
// Inputs hold strings; the API wants numbers, booleans and lists. A blank
// optional number is sent as null, not 0.

const DEFAULTS = {
  count: "1",
  basis: "single",
  experience_basis: "total",
  metric: "average_annual_turnover",
};

/** The drawer's form state for a kind's params, from stored params or blank. */
export function paramsForm(kind, params = {}) {
  const form = {};
  for (const field of CRITERION_KINDS[kind]?.fields ?? []) {
    const stored = params[field.name];
    if (field.type === "checkbox") form[field.name] = Boolean(stored);
    else if (field.type === "list") form[field.name] = Array.isArray(stored) ? stored : [];
    else if (stored === null || stored === undefined) {
      form[field.name] = field.required && DEFAULTS[field.name] !== undefined ? DEFAULTS[field.name] : "";
    } else form[field.name] = String(stored);
  }
  return form;
}

/** The params the API is sent, from the drawer's form state. */
export function paramsPayload(kind, form) {
  const params = {};
  for (const field of visibleFields(kind, form)) {
    const value = form[field.name];
    if (field.type === "checkbox") params[field.name] = Boolean(value);
    else if (field.type === "list") params[field.name] = value;
    else if (["int", "number", "amount"].includes(field.type)) {
      params[field.name] = String(value).trim() === "" ? null : Number(value);
    } else params[field.name] = String(value ?? "").trim() || null;
  }
  return params;
}

/** The first problem with a kind's form, as a sentence, or null. */
export function validateParams(kind, form) {
  for (const field of visibleFields(kind, form)) {
    const value = form[field.name];
    const blank = field.type === "list" ? value.length === 0 : String(value ?? "").trim() === "";
    if (blank) {
      if (field.required && field.type !== "checkbox") return `Please fill in ${field.label}.`;
      continue;
    }
    if (field.type === "list") {
      if (field.max !== undefined && value.length > field.max) {
        return `${field.label} can list at most ${field.max} entries.`;
      }
      continue;
    }
    if (["int", "number", "amount"].includes(field.type)) {
      const number = Number(value);
      if (!Number.isFinite(number)) return `${field.label} must be a number.`;
      if (field.type === "int" && !Number.isInteger(number)) return `${field.label} must be a whole number.`;
      const min = field.min ?? 0;
      if (field.exclusiveMin ? number <= min : number < min) {
        return field.exclusiveMin
          ? `${field.label} must be more than ${min}.`
          : `${field.label} must be at least ${min}.`;
      }
      if (field.max !== undefined && number > field.max) return `${field.label} must be at most ${field.max}.`;
    }
  }
  return null;
}

// --- one line per criterion ------------------------------------------------------

function years(n) {
  return `${n} ${Number(n) === 1 ? "year" : "years"}`;
}

function metricLabel(params) {
  if (params.metric === "other") return params.description || "Financial requirement";
  return FINANCIAL_METRICS.find((m) => m.value === params.metric)?.label ?? params.metric;
}

/** The requirement in one line, the way the list shows it. */
export function criterionSummary(criterion) {
  const p = criterion.params || {};
  switch (criterion.kind) {
    case "similar_projects": {
      let text = `At least ${p.count} similar ${p.count === 1 ? "project" : "projects"}`;
      if (p.work_description) text += ` (${p.work_description})`;
      if (p.min_value_each) text += `, each worth at least ${formatAmount(p.min_value_each)}`;
      if (p.completed_within_years) {
        text += `, ${p.must_be_completed ? "completed" : "executed"} in the last ${years(p.completed_within_years)}`;
      } else if (p.must_be_completed) text += ", completed";
      return text;
    }
    case "project_value": {
      const amount = formatAmount(p.min_value);
      let text =
        p.basis === "total"
          ? `Cited projects totalling at least ${amount}`
          : p.basis === "average"
            ? `Cited projects averaging at least ${amount}`
            : `One project worth at least ${amount}`;
      if (p.within_years) text += `, in the last ${years(p.within_years)}`;
      return text;
    }
    case "personnel": {
      let text = `${p.count ?? 1} × ${p.role}`;
      if (p.min_experience_years !== null && p.min_experience_years !== undefined) {
        text += `, at least ${years(p.min_experience_years)} ${p.experience_basis === "vgil" ? "with VGIL" : "of experience"}`;
      }
      if (p.qualification) text += `, ${p.qualification}`;
      if (p.required_certifications?.length) text += `, holding ${p.required_certifications.join(", ")}`;
      return text;
    }
    case "certification": {
      let text = `Company holds ${p.name}`;
      if (p.issuing_body) text += ` (${p.issuing_body})`;
      text += p.valid_on ? `, valid on ${p.valid_on}` : ", valid on the submission date";
      return text;
    }
    case "document": {
      let text = `${p.count ? `${p.count} × ` : ""}${p.document_type}`;
      if (p.description) text += ` — ${p.description}`;
      if (p.validity_note) text += ` (${p.validity_note})`;
      return text;
    }
    case "financial": {
      let text = `${metricLabel(p)} of at least ${formatAmount(p.min_amount)}`;
      if (p.period_years) {
        text += p.period_years === 1 ? " (last financial year)" : ` (over the last ${p.period_years} financial years)`;
      }
      return text;
    }
    case "other":
      return p.description;
    default:
      return kindLabel(criterion.kind);
  }
}

/** "3 (2 mandatory)", or "Not recorded" -- the Overview's readiness line. */
export function criteriaCountLabel(criteria = []) {
  if (!criteria.length) return "Not recorded";
  const mandatory = criteria.filter((criterion) => criterion.mandatory).length;
  return `${criteria.length} (${mandatory} mandatory)`;
}

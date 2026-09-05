/** The documents a project can hold as evidence for citing in a tender. */
export const EVIDENCE_TYPES = [
  "LOI",
  "Work Order",
  "Completion Certificate",
  "Purchase Order",
  "Contract",
];

/** Every type the document form offers — the same list as the evidence
 * strip, so anything attachable also shows as a pill and can be filtered on. */
export const DOCUMENT_TYPES = EVIDENCE_TYPES;

// Re-exported so the Projects screens keep importing their helpers from one
// place, while the formatting itself is shared with the other areas.
export { dash, formatPeriod, formatTimestamp } from "../../lib/format";

/** { [documentId]: document_type } for the has-document filter and the pills. */
export function documentTypeIndex(documents) {
  const index = {};
  for (const document of documents || []) index[document.id] = document.document_type;
  return index;
}

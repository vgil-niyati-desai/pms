import { daysBetween, localToday } from "../../lib/dates";

/**
 * Document types a tender collects, in the order they arrive.
 *
 * A bid folder holds the issuer's pack (the RFP / ITT / terms of reference,
 * conditions, returnable forms) and VGIL's own response (technical and
 * financial proposals), so those are named rather than all filed as Other.
 * "Submitted Bid" stays for a bid kept as one file. Nothing in the backend
 * depends on these names: a document's type is free text there.
 */
export const TENDER_DOCUMENT_TYPES = [
  "Tender Notice",
  "Tender Document",
  "Corrigendum",
  "Supporting Form",
  "Technical Proposal",
  "Financial Proposal",
  "Submitted Bid",
  "Award Letter",
  "Other",
];

export const TENDER_TYPES = ["Open", "Limited", "Single", "EOI", "RFP", "Other"];

export const SUBMISSION_MODES = ["Online", "Physical", "Both"];

/**
 * How close the deadline is, for the list's deadline column. Only meaningful
 * while a tender is still open, so the caller decides whether to show it.
 */
export function deadlineUrgency(deadline, today = localToday()) {
  if (!deadline) return null;
  if (deadline < today) return "Passed";
  const days = daysBetween(today, deadline);
  if (days === 0) return "Today";
  if (days <= 7) return `${days}d left`;
  return null;
}

/**
 * Whether a certificate obtained for a tender is still inside its validity
 * window. `today` is passed in so the caller decides what "now" means, and so
 * this stays testable.
 */
export function certificateValidity(certificate, today = localToday()) {
  if (!certificate.valid_to) return "No expiry";
  return certificate.valid_to < today ? "Expired" : "Valid";
}

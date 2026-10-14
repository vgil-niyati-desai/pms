import { daysBetween, localToday } from "../../lib/dates";

/** Document types a tender collects, in the order they arrive. */
export const TENDER_DOCUMENT_TYPES = [
  "Tender Notice",
  "Corrigendum",
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

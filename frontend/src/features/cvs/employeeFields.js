import { sortedCvs, certificationStatus } from "../../api/employees";
import { dash } from "../../lib/format";
import { localToday } from "../../lib/dates";

/** The date of the most recent CV on file, for the list column. */
export function latestCvDate(employee) {
  const [newest] = sortedCvs(employee);
  if (!newest) return "—";
  return dash(newest.cv_date || newest.created_at?.slice(0, 10));
}

/** Certification names for the list column, trimmed so the row stays readable. */
export function certificationSummary(employee, limit = 2) {
  const names = employee.certifications.map((certification) => certification.name).filter(Boolean);
  if (names.length === 0) return "—";
  const shown = names.slice(0, limit).join(", ");
  return names.length > limit ? `${shown} +${names.length - limit}` : shown;
}

/** A CV's display name, since the version label is optional. */
export function cvLabel(cv) {
  return cv.version_label || cv.file_name || "Untitled CV";
}

/**
 * The most urgent certification status an employee holds, for a quick flag
 * on the list row — "Expired" outranks "Expiring soon", and both outrank
 * nothing worth flagging. null when there is nothing to call out, so the
 * list column stays quiet for everyone else.
 */
export function certificationAlert(employee, today = localToday()) {
  const statuses = employee.certifications.map((c) => certificationStatus(c, today));
  if (statuses.includes("Expired")) return "Expired";
  if (statuses.includes("Expiring soon")) return "Expiring soon";
  return null;
}

import { sortedCvs } from "../../api/employees";
import { dash } from "../../lib/format";

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

/** Display helpers shared by every area's screens. */

export const dash = (value) =>
  value === null || value === undefined || value === "" ? "—" : value;

/** "2021-05-20 – 2024-09-05", or one side of it, or a dash. */
export function formatPeriod(start, end) {
  if (start && end) return `${start} – ${end}`;
  if (start) return `From ${start}`;
  if (end) return `Until ${end}`;
  return "—";
}

/**
 * A span of experience, in the "7+ yrs" form every CV in this system uses.
 *
 * The backend derives the number from a date, so it is precise to the day and
 * changes on its own. It is shown floored with a "+" rather than to one
 * decimal for two reasons: it is the convention the CVs and tender forms are
 * already written in ("Total Professional Experience: 7+ Years"), and a figure
 * like "7.4 yrs" claims a precision that a start date recorded to the nearest
 * day does not really carry.
 *
 * null is a dash, not a zero. "Not recorded" and "none" are different answers,
 * and only one of them means somebody still has to go and find the date.
 */
export function formatExperience(years) {
  if (years === null || years === undefined || years === "") return "—";
  const value = Number(years);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1) return "under a year";
  return `${Math.floor(value)}+ yrs`;
}

/** ISO timestamp -> just the date, which is all a list column needs. */
export function formatTimestamp(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toISOString().slice(0, 10);
}

/**
 * A money amount, grouped the Indian way (12,00,000) since that is how every
 * value in this system is written. Blank reads as a dash; anything that is not
 * a number is shown as typed rather than silently mangled.
 */
export function formatAmount(value) {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  return `₹ ${amount.toLocaleString("en-IN")}`;
}

/** Adds up amounts that arrive as strings from number inputs. */
export function sumAmounts(values) {
  return values.reduce((total, value) => {
    const amount = Number(value);
    return Number.isFinite(amount) ? total + amount : total;
  }, 0);
}

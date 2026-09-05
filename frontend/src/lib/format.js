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

/** Display helpers shared by every area's screens. */

import { localIsoDate } from "./dates";

export const dash = (value) =>
  value === null || value === undefined || value === "" ? "—" : value;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * An ISO day as "20 May 2021".
 *
 * Anything that is not an ISO day is returned as it was stored rather than
 * blanked: showing the odd value is how anyone finds out it is there.
 */
function formatDay(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  if (!match) return value;
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${match[3]} ${month} ${match[1]}` : value;
}

/** "20 May 2021 – 05 Sep 2024", or one side of it, or a dash. */
export function formatPeriod(start, end) {
  if (start && end) return `${formatDay(start)} – ${formatDay(end)}`;
  if (start) return `From ${formatDay(start)}`;
  if (end) return `Until ${formatDay(end)}`;
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

/**
 * ISO timestamp -> just the date, which is all a list column needs. The day
 * the instant falls on where the user is: a record saved at 02:00 in India is
 * that day's, not the previous one the UTC date would name.
 */
export function formatTimestamp(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : localIsoDate(parsed);
}

/**
 * A money amount, grouped the Indian way (12,00,000) since that is how every
 * value in this system is written. Blank reads as a dash; anything that is not
 * a number is shown as typed rather than silently mangled.
 */
export function formatAmount(value) {
  // Blank after trimming counts as blank: a field holding only spaces has no
  // amount in it, and reading it as "₹ 0" would state a figure nobody entered.
  // The number 0 is not caught by this -- "0" does not trim away -- so a real
  // zero still reads as one.
  if (value === null || value === undefined || String(value).trim() === "") return "—";
  // Grouping is stripped before the number is read, because the money fields
  // these come from are free text and "5,00,000" is how they are usually
  // written. The server already reads them this way -- the _number_key stages
  // in the project and tender routers strip commas and spaces before they
  // compare or sort -- so displaying them without that was stricter than
  // sorting them: the same value that sorted as five hundred thousand read
  // back as raw text.
  const amount = Number(String(value).replace(/[,\s]/g, ""));
  // Anything that still does not parse is returned exactly as stored, not
  // stripped -- "TBD", "As per BOQ", a figure with a currency word in it.
  // Showing the odd value is how anyone finds out it is there.
  if (!Number.isFinite(amount)) return String(value);
  return `₹ ${amount.toLocaleString("en-IN")}`;
}

/** A file size in bytes as "240 KB" / "3.1 MB", for confirming what was
 *  picked in a file input before it is uploaded. */
export function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Adds up amounts that arrive as strings from number inputs. */
export function sumAmounts(values) {
  return values.reduce((total, value) => {
    const amount = Number(value);
    return Number.isFinite(amount) ? total + amount : total;
  }, 0);
}

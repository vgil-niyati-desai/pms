/**
 * Calendar dates, as the YYYY-MM-DD strings every date-only field here holds.
 *
 * Two kinds of value look alike and must not be mixed up:
 *
 *   * A date-only field -- an expiry, a deadline, a validity date -- names a
 *     day on the calendar. It has no time and no time zone, and is compared
 *     as a string, which orders correctly for YYYY-MM-DD.
 *   * A timestamp -- created_at, updated_at -- names an instant. Which day it
 *     falls on depends on where it is read.
 *
 * "Today" is a calendar day, so it is the day on the user's own clock. The
 * UTC date, which `toISOString().slice(0, 10)` gives, is still yesterday in
 * India until 05:30 -- long enough for a certificate that lapsed at midnight
 * to keep reading as valid.
 */

function pad(number) {
  return String(number).padStart(2, "0");
}

/** The day an instant falls on, on the user's own calendar. */
export function localIsoDate(instant) {
  return `${instant.getFullYear()}-${pad(instant.getMonth() + 1)}-${pad(instant.getDate())}`;
}

/** Today, on the user's own calendar. */
export function localToday() {
  return localIsoDate(new Date());
}

/** Midnight UTC of a date-only value: a fixed point to count whole days from. */
function dayNumber(isoDate) {
  const [year, month, day] = isoDate.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

/**
 * A date-only value moved by whole days. Worked out on the UTC calendar, which
 * has no daylight-saving gaps, so the day never slips whatever the user's
 * zone is.
 */
export function addDays(isoDate, days) {
  return new Date(dayNumber(isoDate) + days * 86400000).toISOString().slice(0, 10);
}

/** Whole days from one date-only value to another (negative if `to` is earlier). */
export function daysBetween(from, to) {
  return Math.round((dayNumber(to) - dayNumber(from)) / 86400000);
}

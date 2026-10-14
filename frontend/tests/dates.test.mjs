// Date-only status checks around midnight, in India and elsewhere.
//
// Run from the frontend folder:  npm test   (node --test, no dependencies)
//
// Every check pins "now" to an exact instant and the time zone to a named
// one, so the results never depend on when or where the suite is run.

import { register } from "node:module";
import { afterEach, describe, test } from "node:test";
import assert from "node:assert/strict";

// The app's own imports leave off ".js" (Vite resolves them); Node does not,
// so add it back for relative paths.
register(
  "data:text/javascript," +
    encodeURIComponent(`
      export async function resolve(specifier, context, next) {
        try {
          return await next(specifier, context);
        } catch (error) {
          if (error.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier)) {
            return next(specifier + ".js", context);
          }
          throw error;
        }
      }
    `),
);

const { localIsoDate, localToday, addDays, daysBetween } = await import("../src/lib/dates.js");
const { formatTimestamp } = await import("../src/lib/format.js");
const { certificationStatus, listCertifications } = await import("../src/api/employees.js");
const { certificationAlert } = await import("../src/features/cvs/employeeFields.js");
const { deadlineUrgency, certificateValidity } = await import("../src/features/tenders/tenderFields.js");

const RealDate = Date;
const realFetch = globalThis.fetch;

/** Pin the clock to `iso` (an instant) and the zone to `zone` for one check. */
function at(iso, zone) {
  process.env.TZ = zone;
  const now = new RealDate(iso).getTime();
  globalThis.Date = class extends RealDate {
    constructor(...args) {
      super(...(args.length ? args : [now]));
    }
    static now() {
      return now;
    }
  };
}

afterEach(() => {
  globalThis.Date = RealDate;
  globalThis.fetch = realFetch;
  process.env.TZ = "Asia/Kolkata";
});

// 2026-10-07T18:45:00Z is 00:15 on 8 October in India, while the UTC date is
// still the 7th -- the window the old toISOString() "today" got wrong.
const JUST_AFTER_IST_MIDNIGHT = "2026-10-07T18:45:00Z";
const JUST_BEFORE_IST_MIDNIGHT = "2026-10-08T18:29:59Z"; // 23:59:59 on the 8th
const IST_MIDNIGHT = "2026-10-08T18:30:00Z"; // 00:00:00 on the 9th

describe("today is the local calendar day", () => {
  test("in India just after midnight, it is already the new day", () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata");
    assert.equal(localToday(), "2026-10-08");
    assert.equal(new RealDate(JUST_AFTER_IST_MIDNIGHT).toISOString().slice(0, 10), "2026-10-07");
  });
  test("one second before midnight it is still that day; at midnight the next", () => {
    at(JUST_BEFORE_IST_MIDNIGHT, "Asia/Kolkata");
    assert.equal(localToday(), "2026-10-08");
    at(IST_MIDNIGHT, "Asia/Kolkata");
    assert.equal(localToday(), "2026-10-09");
  });
  test("the same instant is a different day in a zone behind UTC", () => {
    at("2026-10-08T03:00:00Z", "America/Los_Angeles"); // 20:00 on the 7th there
    assert.equal(localToday(), "2026-10-07");
  });
});

describe("date-only arithmetic never slips a day", () => {
  for (const zone of ["Asia/Kolkata", "UTC", "America/Los_Angeles", "Pacific/Kiritimati"]) {
    test(`in ${zone}`, () => {
      process.env.TZ = zone;
      assert.equal(addDays("2026-10-08", 30), "2026-11-07");
      assert.equal(addDays("2026-12-15", 30), "2027-01-14"); // year boundary
      assert.equal(addDays("2028-02-01", 30), "2028-03-02"); // leap February
      assert.equal(addDays("2026-03-01", -1), "2026-02-28");
      assert.equal(daysBetween("2026-10-08", "2026-10-15"), 7);
      assert.equal(daysBetween("2026-10-08", "2026-10-08"), 0);
      assert.equal(daysBetween("2026-10-08", "2026-10-07"), -1);
      // Across the US clock change (1 Nov 2026): still whole days.
      assert.equal(daysBetween("2026-10-31", "2026-11-02"), 2);
    });
  }
});

describe("certification status (employee tab, list alert)", () => {
  test("a certificate that lapsed yesterday is Expired from 00:15 in India", () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata"); // today = 2026-10-08
    assert.equal(certificationStatus({ expiry_date: "2026-10-07" }), "Expired");
    assert.equal(certificationStatus({ expiry_date: "2026-10-08" }), "Expiring soon");
  });
  test("the 30-day window is unchanged: day 30 is soon, day 31 is valid", () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata");
    assert.equal(certificationStatus({ expiry_date: "2026-11-07" }), "Expiring soon");
    assert.equal(certificationStatus({ expiry_date: "2026-11-08" }), "Valid");
    assert.equal(certificationStatus({ expiry_date: "" }), "No expiry");
    assert.equal(certificationStatus({ expiry_date: null }), "No expiry");
  });
  test("an ordinary afternoon gives the same answers as an explicit today", () => {
    at("2026-10-08T09:00:00Z", "Asia/Kolkata"); // 14:30 on the 8th
    for (const expiry of ["2025-01-01", "2026-10-07", "2026-10-08", "2026-11-07", "2026-11-08", "2031-01-01"]) {
      assert.equal(
        certificationStatus({ expiry_date: expiry }),
        certificationStatus({ expiry_date: expiry }, "2026-10-08"),
        expiry,
      );
    }
  });
  test("the list alert flags the most urgent status", () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata");
    const employee = (dates) => ({ certifications: dates.map((expiry_date) => ({ expiry_date })) });
    assert.equal(certificationAlert(employee(["2026-10-07", "2026-10-20"])), "Expired");
    assert.equal(certificationAlert(employee(["2026-10-20", "2031-01-01"])), "Expiring soon");
    assert.equal(certificationAlert(employee(["2031-01-01", ""])), null);
  });
});

describe("certification index request (certifications view, dashboard)", () => {
  test("sends the local day as today", async () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata");
    let requested = null;
    globalThis.fetch = async (url) => {
      requested = new URL(url);
      return new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 1 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    await listCertifications({ status: "Expired", page: 1, pageSize: 1 });
    assert.equal(requested.searchParams.get("today"), "2026-10-08");
  });
});

describe("tender date flags", () => {
  test("deadline urgency counts from the local day", () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata"); // today = 2026-10-08
    assert.equal(deadlineUrgency("2026-10-07"), "Passed");
    assert.equal(deadlineUrgency("2026-10-08"), "Today");
    assert.equal(deadlineUrgency("2026-10-09"), "1d left");
    assert.equal(deadlineUrgency("2026-10-15"), "7d left");
    assert.equal(deadlineUrgency("2026-10-16"), null);
    assert.equal(deadlineUrgency(""), null);
  });
  test("deadline urgency is the same west of UTC for the same local day", () => {
    at("2026-10-08T16:00:00Z", "America/Los_Angeles"); // 09:00 on the 8th there
    assert.equal(deadlineUrgency("2026-10-08"), "Today");
    assert.equal(deadlineUrgency("2026-10-15"), "7d left");
  });
  test("a tender certificate that ended yesterday is Expired", () => {
    at(JUST_AFTER_IST_MIDNIGHT, "Asia/Kolkata");
    assert.equal(certificateValidity({ valid_to: "2026-10-07" }), "Expired");
    assert.equal(certificateValidity({ valid_to: "2026-10-08" }), "Valid");
    assert.equal(certificateValidity({ valid_to: "" }), "No expiry");
  });
});

describe("timestamps stay instants, shown on the local day", () => {
  test("an instant just after midnight in India shows India's date", () => {
    process.env.TZ = "Asia/Kolkata";
    assert.equal(formatTimestamp("2026-10-07T20:30:00Z"), "2026-10-08"); // 02:00 IST on the 8th
    assert.equal(formatTimestamp("2026-10-07T18:29:59Z"), "2026-10-07"); // 23:59:59 IST on the 7th
    assert.equal(formatTimestamp("2026-10-07T10:00:00Z"), "2026-10-07");
  });
  test("an explicit offset is honoured", () => {
    process.env.TZ = "Asia/Kolkata";
    assert.equal(formatTimestamp("2026-10-08T00:30:00+05:30"), "2026-10-08");
  });
  test("blank or unreadable values are a dash", () => {
    assert.equal(formatTimestamp(null), "—");
    assert.equal(formatTimestamp(""), "—");
    assert.equal(formatTimestamp("not a date"), "—");
  });
  test("localIsoDate pads month and day", () => {
    process.env.TZ = "UTC";
    assert.equal(localIsoDate(new RealDate("2026-01-05T12:00:00Z")), "2026-01-05");
  });
});

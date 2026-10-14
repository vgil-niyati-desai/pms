// The Dashboard's summary counts: one failed request must not blank the rest.
//
// Run from the frontend folder:  npm test

import { describe, test } from "node:test";
import assert from "node:assert/strict";

const { settleStats, statsErrors } = await import("../src/lib/dashboardStats.js");

const ok = (total) => () => Promise.resolve({ total, items: [] });
const fail = (message) => () => Promise.reject(new Error(message));

describe("settleStats", () => {
  test("returns every count when all four requests succeed", async () => {
    const stats = await settleStats({
      ongoing: ok(3),
      completed: ok(7),
      employees: ok(12),
      certificationsExpired: ok(0),
    });
    assert.deepEqual(stats, {
      ongoing: { value: 3 },
      completed: { value: 7 },
      employees: { value: 12 },
      certificationsExpired: { value: 0 },
    });
    assert.deepEqual(statsErrors(stats), []);
  });

  test("keeps the other three counts when one request fails", async () => {
    const stats = await settleStats({
      ongoing: ok(3),
      completed: ok(7),
      employees: ok(12),
      certificationsExpired: fail("Failed to load certifications"),
    });
    assert.deepEqual(stats.ongoing, { value: 3 });
    assert.deepEqual(stats.completed, { value: 7 });
    assert.deepEqual(stats.employees, { value: 12 });
    assert.deepEqual(stats.certificationsExpired, { error: "Failed to load certifications" });
    // A failure is never reported as a zero count.
    assert.equal(stats.certificationsExpired.value, undefined);
    assert.deepEqual(statsErrors(stats), ["Failed to load certifications"]);
  });

  test("keeps successes when several fail, and survives all failing", async () => {
    const some = await settleStats({
      ongoing: fail("boom"),
      completed: ok(7),
      employees: fail("boom"),
      certificationsExpired: ok(2),
    });
    assert.deepEqual(some.completed, { value: 7 });
    assert.deepEqual(some.certificationsExpired, { value: 2 });
    assert.deepEqual(some.ongoing, { error: "boom" });
    assert.deepEqual(statsErrors(some), ["boom"]);

    const none = await settleStats({
      ongoing: fail("a"),
      completed: fail("b"),
      employees: () => Promise.reject("no message"),
      certificationsExpired: fail("a"),
    });
    assert.ok(Object.values(none).every((s) => s.error && s.value === undefined));
    assert.equal(none.employees.error, "Something went wrong.");
    assert.deepEqual(statsErrors(none), ["a", "b", "Something went wrong."]);
  });
});

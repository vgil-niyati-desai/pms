// The app-wide unsaved-changes guard: which surfaces count as dirty, and
// which navigations are stopped to ask.
//
// Run from the frontend folder:  npm test

import { describe, test } from "node:test";
import assert from "node:assert/strict";

const { createDirtyRegistry, shouldBlockNavigation } = await import(
  "../src/lib/unsavedChanges.js"
);

const at = (pathname, search = "", hash = "") => ({ pathname, search, hash });

describe("createDirtyRegistry", () => {
  test("is clean until something registers", () => {
    const registry = createDirtyRegistry();
    assert.equal(registry.anyDirty(), false);
  });

  test("stays dirty while any one surface is still registered", () => {
    const registry = createDirtyRegistry();
    registry.add("drawer");
    registry.add("redactor");
    registry.remove("drawer");
    assert.equal(registry.anyDirty(), true);
    registry.remove("redactor");
    assert.equal(registry.anyDirty(), false);
  });

  test("adding or removing the same id twice changes nothing", () => {
    const registry = createDirtyRegistry();
    registry.add("form");
    registry.add("form");
    registry.remove("form");
    assert.equal(registry.anyDirty(), false);
    registry.remove("form");
    assert.equal(registry.anyDirty(), false);
  });
});

describe("shouldBlockNavigation", () => {
  test("never blocks when nothing is dirty", () => {
    assert.equal(shouldBlockNavigation(false, at("/projects/1/edit"), at("/projects")), false);
  });

  test("blocks a dirty screen leaving for another path", () => {
    assert.equal(shouldBlockNavigation(true, at("/projects/1/edit"), at("/projects")), true);
    assert.equal(shouldBlockNavigation(true, at("/projects/1/edit"), at("/projects/new")), true);
    assert.equal(shouldBlockNavigation(true, at("/tenders/7", "?tab=costs&cost=new"), at("/")), true);
  });

  test("lets a drawer close itself through the query string", () => {
    assert.equal(
      shouldBlockNavigation(true, at("/tenders/7", "?tab=costs&cost=new"), at("/tenders/7", "?tab=costs")),
      false,
    );
    assert.equal(shouldBlockNavigation(true, at("/cvs/3", "?tab=cvs&cv=new"), at("/cvs/3")), false);
  });

  test("ignores a hash-only change", () => {
    assert.equal(shouldBlockNavigation(true, at("/documents"), at("/documents", "", "#top")), false);
  });
});

// fetchDocumentFile: the backend's own sentence when a file cannot be served,
// and the bytes when it can.
//
// Run from the frontend folder:  npm test   (node --test, no dependencies)
//
// fetch() is stubbed per test with a canned Response, so nothing here needs
// the backend. What is pinned down is the contract the preview relies on: a
// 404 for a record whose file is gone from disk must surface as a readable
// Error, not as JSON in a frame or a broken image.

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

const { fetchDocumentFile, API_BASE_URL } = await import("../src/api/documents.js");
const { UNREACHABLE_MESSAGE } = await import("../src/api/http.js");

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

/** Replace fetch with one that records the URL and answers `respond()`. */
function answerWith(respond) {
  const calls = [];
  globalThis.fetch = (url) => {
    calls.push(String(url));
    return respond();
  };
  return calls;
}

const json = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("fetchDocumentFile", () => {
  test("asks the backend for the inline file", async () => {
    const calls = answerWith(() => Promise.resolve(new Response("%PDF-1.4", { status: 200 })));
    await fetchDocumentFile("abc123");
    assert.deepEqual(calls, [`${API_BASE_URL}/documents/abc123/file?disposition=inline`]);
  });

  test("a served file comes back as its bytes and media type", async () => {
    answerWith(() =>
      Promise.resolve(
        new Response("%PDF-1.4", {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        }),
      ),
    );
    const blob = await fetchDocumentFile("abc123");
    assert.equal(blob.type, "application/pdf");
    assert.equal(await blob.text(), "%PDF-1.4");
  });

  test("a file missing from disk is reported in the backend's own words", async () => {
    answerWith(() => Promise.resolve(json(404, { detail: "File missing on server" })));
    await assert.rejects(fetchDocumentFile("abc123"), { message: "File missing on server" });
  });

  test("a record with no file is reported the same way", async () => {
    answerWith(() => Promise.resolve(json(404, { detail: "No file for this document" })));
    await assert.rejects(fetchDocumentFile("abc123"), { message: "No file for this document" });
  });

  test("an error without a readable body falls back to a plain sentence", async () => {
    answerWith(() => Promise.resolve(new Response("Internal Server Error", { status: 500 })));
    await assert.rejects(fetchDocumentFile("abc123"), { message: "Failed to load the file" });
  });

  test("a backend that cannot be reached says so instead of 'Failed to fetch'", async () => {
    answerWith(() => Promise.reject(new TypeError("Failed to fetch")));
    await assert.rejects(fetchDocumentFile("abc123"), { message: UNREACHABLE_MESSAGE });
  });
});

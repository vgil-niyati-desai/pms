import { useCallback, useEffect, useState } from "react";
import { fetchDocumentFile } from "../api/documents";

const EMPTY = { blob: null, url: null, mediaType: "", error: null, loading: false };

/**
 * Loads a document's stored file once and hands back an object URL to show
 * it from, the Blob to save it from, and the backend's own sentence when it
 * could not be served.
 *
 * Pointing an iframe or an img at the file route directly cannot tell a
 * missing file from a slow one: a cross-origin frame reports no status, so
 * the 404 body rendered as text and the img as a broken picture. Fetching
 * first turns that into `error`, which the preview shows in place.
 *
 * `mediaType` is the served Content-Type, which the backend derives from the
 * stored file. The preview prefers it to the file name's extension, so a
 * record whose copied name has gone stale -- a PDF that replaced a JPG --
 * still renders as what it is.
 *
 * The object URL is revoked when the document changes or the caller unmounts.
 * `reload` fetches again, for a Retry after a failure.
 */
export default function useDocumentFile(documentId) {
  const [state, setState] = useState(documentId ? { ...EMPTY, loading: true } : EMPTY);
  const [attempt, setAttempt] = useState(0);

  const reload = useCallback(() => setAttempt((count) => count + 1), []);

  useEffect(() => {
    if (!documentId) {
      setState(EMPTY);
      return undefined;
    }
    let current = true;
    let url = null;
    setState({ ...EMPTY, loading: true });
    fetchDocumentFile(documentId)
      .then((blob) => {
        if (!current) return;
        url = URL.createObjectURL(blob);
        setState({ blob, url, mediaType: blob.type || "", error: null, loading: false });
      })
      .catch((err) => {
        if (current) setState({ ...EMPTY, error: err.message || "Could not load the file." });
      });
    return () => {
      current = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [documentId, attempt]);

  return { ...state, reload };
}

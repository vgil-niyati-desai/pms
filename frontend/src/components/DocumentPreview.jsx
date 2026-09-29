import { useCallback, useId, useRef, useState } from "react";
import useEscapeKey from "../hooks/useEscapeKey";
import DocumentRedactor from "./DocumentRedactor";
import FilePreview from "./FilePreview";
import Modal from "./Modal";
import { canRedact, documentFileUrl } from "../api/documents";

/**
 * Full-screen viewer for one uploaded file.
 *
 * Clicking View used to hand the file to the browser, which downloaded it and
 * left the list behind. This shows it in the app instead: a PDF in a frame or
 * a PNG/JPG as an image, with Download still one click away for when the file
 * is actually wanted on disk.
 *
 * Redact swaps the body for the selection view, where areas are marked and a
 * permanently redacted *copy* is generated. It is a mode of this viewer
 * rather than a screen of its own, because it is the same document being
 * looked at — and because the way back is the same Close. Nothing in that
 * mode touches the entry: the original stays exactly as uploaded, and the
 * preview and Download above go on serving it.
 *
 * Dismissed by the Close button, the backdrop, or Escape — the same three
 * routes out as Modal and Drawer, so it behaves like every other layer.
 * While redacting, the backdrop and Escape back out of redaction first
 * rather than closing the viewer. Marked areas exist only on this screen, so
 * any route that would drop them — those two, Close, and the redactor's own
 * Cancel — asks first when there is something to lose, and does exactly
 * what was asked once the loss is confirmed.
 */
export default function DocumentPreview({ documentId, fileName, title, onClose }) {
  const titleId = useId();
  const [redacting, setRedacting] = useState(false);
  // Read at the moment a route out is taken rather than held as state, so a
  // box drawn a moment before Escape is never missed by a render that has
  // not happened yet.
  const redactDirtyRef = useRef(false);
  const reportDirty = useCallback((dirty) => {
    redactDirtyRef.current = dirty;
  }, []);
  // What to do once unsaved marks are confirmed as discarded: "close" the
  // viewer, or go "back" from redaction to the plain preview.
  const [pendingExit, setPendingExit] = useState(null);

  function exit(kind) {
    if (kind === "close") {
      onClose();
      return;
    }
    setRedacting(false);
    redactDirtyRef.current = false;
  }

  function requestExit(kind) {
    if (redacting && redactDirtyRef.current) setPendingExit(kind);
    else exit(kind);
  }

  function requestClose() {
    requestExit(redacting ? "back" : "close");
  }

  function discard() {
    const kind = pendingExit;
    setPendingExit(null);
    exit(kind);
  }

  // While the confirmation is up it is the layer Escape belongs to, and it
  // registers after this one, so Escape cancels it and leaves the viewer be.
  useEscapeKey(true, requestClose);

  const previewUrl = documentFileUrl(documentId, { inline: true });
  const downloadUrl = documentFileUrl(documentId);
  const heading = title || fileName || "Document";
  const redactable = canRedact(fileName);

  return (
    <div className="preview-backdrop" onClick={requestClose}>
      <div
        className="preview-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="preview-head">
          <div className="preview-title">
            <h2 id={titleId}>{heading}</h2>
            {fileName && heading !== fileName && <p className="muted">{fileName}</p>}
            {redacting && <p className="muted">Marking areas to redact</p>}
          </div>
          <div className="preview-actions">
            {redactable && !redacting && (
              <button type="button" className="btn" onClick={() => setRedacting(true)}>
                Redact
              </button>
            )}
            {/* A plain link, not a fetch: the backend's default
                Content-Disposition is attachment, so following it saves the
                file under its original name without leaving the page. */}
            <a className="btn btn-primary" href={downloadUrl}>
              Download
            </a>
            <button type="button" className="btn" onClick={() => requestExit("close")}>
              Close
            </button>
          </div>
        </header>
        <div className={redacting ? "preview-body preview-body-redact" : "preview-body"}>
          {redacting ? (
            <DocumentRedactor
              documentId={documentId}
              fileName={fileName}
              onCancel={() => requestExit("back")}
              onDirtyChange={reportDirty}
            />
          ) : (
            <FilePreview url={previewUrl} fileName={fileName} fill />
          )}
        </div>
      </div>

      {pendingExit && (
        // Its own backdrop click must not also reach the viewer's, which
        // would treat it as a second request to close.
        <div onClick={(e) => e.stopPropagation()}>
          <Modal title="Discard marked areas?" onClose={() => setPendingExit(null)}>
            <p>
              The areas marked for redaction have not been saved as a redacted copy.
              {pendingExit === "close" ? " Closing" : " Leaving redaction"} discards them;
              the original document is not affected either way.
            </p>
            <div className="form-actions">
              <button type="button" className="btn btn-danger" onClick={discard}>
                Discard changes
              </button>
              <button type="button" className="btn" onClick={() => setPendingExit(null)}>
                Keep editing
              </button>
            </div>
          </Modal>
        </div>
      )}
    </div>
  );
}

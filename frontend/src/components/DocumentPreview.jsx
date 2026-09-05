import { useId, useState } from "react";
import useEscapeKey from "../hooks/useEscapeKey";
import DocumentRedactor from "./DocumentRedactor";
import FilePreview from "./FilePreview";
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
 * While redacting, those three back out of redaction first, so a stray click
 * cannot throw away a page of marked areas.
 */
export default function DocumentPreview({ documentId, fileName, title, onClose }) {
  const titleId = useId();
  const [redacting, setRedacting] = useState(false);

  function requestClose() {
    if (redacting) setRedacting(false);
    else onClose();
  }

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
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
          </div>
        </header>
        <div className={redacting ? "preview-body preview-body-redact" : "preview-body"}>
          {redacting ? (
            <DocumentRedactor
              documentId={documentId}
              fileName={fileName}
              onCancel={() => setRedacting(false)}
            />
          ) : (
            <FilePreview url={previewUrl} fileName={fileName} fill />
          )}
        </div>
      </div>
    </div>
  );
}

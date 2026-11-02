import { useEffect, useState } from "react";
import { getRedactionSource, redactionPageImageUrl } from "../api/documents";

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg"];

function extensionOf(fileName) {
  const match = /\.([a-z0-9]+)$/i.exec(fileName || "");
  return match ? match[1].toLowerCase() : "";
}

/**
 * "pdf", "image" or "" for a file: from the media type the backend served it
 * as when there is one, else from the file name's extension.
 *
 * The media type wins because it comes from the stored file itself, while the
 * name may be a copy held by a CV or a cost item that went stale when the
 * file was replaced with one of another type. An unrecognised type -- a
 * generic octet-stream, say -- falls through to the name.
 */
/** Whether the server can render this file as pages (PDF and images only). */
function isPageRenderable(fileName) {
  const extension = extensionOf(fileName);
  return !extension || extension === "pdf" || IMAGE_EXTENSIONS.includes(extension);
}

function kindOf(mediaType, fileName) {
  if (mediaType === "application/pdf") return "pdf";
  if (mediaType && mediaType.startsWith("image/")) return "image";
  const extension = extensionOf(fileName);
  if (extension === "pdf") return "pdf";
  if (IMAGE_EXTENSIONS.includes(extension)) return "image";
  return "";
}

function DocumentPagePreview({ documentId, fileName, fill }) {
  const [source, setSource] = useState(null);
  const [error, setError] = useState("");
  const [pageIndex, setPageIndex] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setSource(null);
    setError("");
    setPageIndex(0);
    getRedactionSource(documentId)
      .then((result) => {
        if (current) setSource(result);
      })
      .catch((err) => {
        if (current) setError(err.message || "Could not prepare the file preview.");
      });
    return () => {
      current = false;
    };
  }, [documentId, attempt]);

  const pages = source?.pages ?? [];
  const page = pages[pageIndex];
  const className = fill ? "file-preview file-preview-fill" : "file-preview";

  if (error) {
    return (
      <div className="document-page-preview-error">
        <p className="alert alert-error">{error}</p>
        <button type="button" className="btn btn-sm" onClick={() => setAttempt((value) => value + 1)}>
          Retry
        </button>
      </div>
    );
  }
  if (!page) {
    if (source && pages.length === 0) {
      return <p className="alert alert-error">This file has no pages to preview.</p>;
    }
    return <p className="muted">Preparing {fileName || "the file"} preview…</p>;
  }

  return (
    <div className={fill ? "document-page-preview document-page-preview-fill" : "document-page-preview"}>
      {pages.length > 1 && (
        <div className="document-page-controls">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setPageIndex((index) => index - 1)}
            disabled={pageIndex === 0}
            aria-label="Previous document page"
          >
            Previous
          </button>
          <span className="muted">
            Page {pageIndex + 1} of {pages.length}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setPageIndex((index) => index + 1)}
            disabled={pageIndex >= pages.length - 1}
            aria-label="Next document page"
          >
            Next
          </button>
        </div>
      )}
      <img
        className={`${className} document-page-image`}
        src={redactionPageImageUrl(documentId, page.index)}
        alt={`${fileName || "Document"} — page ${pageIndex + 1}`}
        onError={() => setError("Could not render this file page. Retry the preview.")}
      />
    </div>
  );
}

/**
 * Inline preview of an uploaded document or a generated file.
 *
 * Uploaded files are rendered as server-generated page images so the preview
 * does not depend on browser support for PDF files or cross-origin file fetches.
 *
 * Generated redacted copies have no document id, so they use the supplied
 * object URL and media type instead.
 */
export default function FilePreview({
  documentId,
  url,
  fileName,
  mediaType = "",
  loading = false,
  error = null,
  fill = false,
}) {
  if (documentId) {
    // Word, Excel and other files are stored and downloadable but cannot be
    // drawn as pages, so say so instead of showing the renderer's error.
    if (!isPageRenderable(fileName)) {
      return (
        <p className="muted">
          No preview for this file type. Download {fileName || "the file"} to open it.
        </p>
      );
    }
    return <DocumentPagePreview documentId={documentId} fileName={fileName} fill={fill} />;
  }
  if (error) return <div className="alert alert-error">{error}</div>;
  if (loading) return <p className="muted">Loading {fileName || "the file"}…</p>;
  if (!url) return <p className="muted">No file attached.</p>;

  const kind = kindOf(mediaType, fileName);
  const className = fill ? "file-preview file-preview-fill" : "file-preview";

  if (kind === "pdf") {
    return <iframe className={className} src={url} title={fileName || "Document preview"} />;
  }
  if (kind === "image") {
    return <img className={className} src={url} alt={fileName || "Document preview"} />;
  }
  return (
    <p className="muted">
      No preview for this file type. Download {fileName || "the file"} to open it.
    </p>
  );
}

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
function kindOf(mediaType, fileName) {
  if (mediaType === "application/pdf") return "pdf";
  if (mediaType && mediaType.startsWith("image/")) return "image";
  const extension = extensionOf(fileName);
  if (extension === "pdf") return "pdf";
  if (IMAGE_EXTENSIONS.includes(extension)) return "image";
  return "";
}

/**
 * Inline preview of an uploaded document.
 *
 * PDFs render in an iframe and images in an img; anything else (and any
 * record with no file) falls back to a message, since nothing here can
 * sensibly show it.
 *
 * `url` is an object URL from useDocumentFile, which has already fetched the
 * file and learnt whether it could be served. That is what `loading` and
 * `error` report: a file missing on the server shows the backend's sentence
 * here, where an iframe pointed at the route would have shown the error
 * JSON as text and an img a broken picture. `mediaType` is the served type,
 * preferred to the name for choosing how to show the file (see kindOf).
 * `fill` makes the preview take the height of its container rather than the
 * fixed height used inside a drawer.
 */
export default function FilePreview({
  url,
  fileName,
  mediaType = "",
  loading = false,
  error = null,
  fill = false,
}) {
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

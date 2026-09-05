const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg"];

function extensionOf(fileName) {
  const match = /\.([a-z0-9]+)$/i.exec(fileName || "");
  return match ? match[1].toLowerCase() : "";
}

/**
 * Inline preview of an uploaded document.
 *
 * PDFs render in an iframe and images in an img; anything else (and any
 * record with no file) falls back to a message, since nothing here can
 * sensibly show it.
 *
 * `url` must be a preview URL — `documentFileUrl(id, { inline: true })` — or
 * the browser downloads the PDF instead of rendering it in the frame.
 * `fill` makes the preview take the height of its container rather than the
 * fixed height used inside a drawer.
 */
export default function FilePreview({ url, fileName, fill = false }) {
  if (!url) return <p className="muted">No file attached.</p>;

  const extension = extensionOf(fileName);
  const className = fill ? "file-preview file-preview-fill" : "file-preview";

  if (extension === "pdf") {
    return <iframe className={className} src={url} title={fileName || "Document preview"} />;
  }
  if (IMAGE_EXTENSIONS.includes(extension)) {
    return <img className={className} src={url} alt={fileName || "Document preview"} />;
  }
  return (
    <p className="muted">
      No preview for this file type. Download {fileName || "the file"} to open it.
    </p>
  );
}

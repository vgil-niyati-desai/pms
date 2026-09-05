import { useState } from "react";
import DocumentPreview from "./DocumentPreview";
import FilePreview from "./FilePreview";
import { documentFileUrl } from "../api/documents";

/**
 * The file attached to the record a drawer is showing: a preview of it, with
 * Download beside a full-screen view for when the drawer is too narrow to
 * read the document in.
 *
 * Every drawer that carries an upload — documents, CVs, certifications,
 * tender costs and certificates — shows the same three things, so they share
 * this rather than each repeating it.
 */
export default function DocumentFileSection({ documentId, fileName, heading = "File", title }) {
  const [previewing, setPreviewing] = useState(false);

  return (
    <section className="drawer-preview">
      <h3>{heading}</h3>
      {documentId ? (
        <>
          <div className="preview-toolbar">
            <button type="button" className="btn btn-sm" onClick={() => setPreviewing(true)}>
              Full preview
            </button>
            <a className="btn btn-sm" href={documentFileUrl(documentId)}>
              Download
            </a>
          </div>
          <FilePreview url={documentFileUrl(documentId, { inline: true })} fileName={fileName} />
          {previewing && (
            <DocumentPreview
              documentId={documentId}
              fileName={fileName}
              title={title}
              onClose={() => setPreviewing(false)}
            />
          )}
        </>
      ) : (
        <FilePreview url={null} fileName={fileName} />
      )}
    </section>
  );
}

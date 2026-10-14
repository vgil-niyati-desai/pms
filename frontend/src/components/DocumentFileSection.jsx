import { useState } from "react";
import useDocumentFile from "../hooks/useDocumentFile";
import DocumentPreview from "./DocumentPreview";
import FilePreview from "./FilePreview";
import { saveBlob } from "../lib/download";

/**
 * The file attached to the record a drawer is showing: a preview of it, with
 * Download beside a full-screen view for when the drawer is too narrow to
 * read the document in.
 *
 * Every drawer that carries an upload — documents, CVs, certifications,
 * tender costs and certificates — shows the same three things, so they share
 * this rather than each repeating it.
 *
 * The file is fetched through useDocumentFile, so a file the server cannot
 * serve is reported here in the backend's own words rather than as error
 * JSON in the frame, and Download saves the bytes already in hand instead of
 * linking to the server -- a link navigated the app away to that JSON.
 */
export default function DocumentFileSection({ documentId, fileName, heading = "File", title }) {
  const [previewing, setPreviewing] = useState(false);
  const file = useDocumentFile(documentId);

  return (
    <section className="drawer-preview">
      <h3>{heading}</h3>
      {documentId ? (
        <>
          <div className="preview-toolbar">
            <button type="button" className="btn btn-sm" onClick={() => setPreviewing(true)}>
              Full preview
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => saveBlob(file.blob, fileName)}
              disabled={!file.blob}
            >
              Download
            </button>
            {file.error && (
              <button type="button" className="btn btn-sm" onClick={file.reload}>
                Retry
              </button>
            )}
          </div>
          <FilePreview
            url={file.url}
            fileName={fileName}
            mediaType={file.mediaType}
            loading={file.loading}
            error={file.error}
          />
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

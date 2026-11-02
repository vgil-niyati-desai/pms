import { useRef, useState } from "react";
import DocumentPreview from "./DocumentPreview";

/**
 * The File cell in every list: opens the document in the in-app preview
 * rather than sending the browser off to download it.
 *
 * It owns the open/closed state so a list only has to render the cell, which
 * is why this is a component rather than a helper returning markup.
 *
 * `onChanged`, when given, is called as the viewer closes if a redacted copy
 * was saved to the document log while it was open. A list that shows the log
 * passes its reload here; every other list leaves it out and is unaffected.
 * Reported on close rather than on save on purpose: a reload swaps the table
 * for its skeleton, which would unmount this cell and the viewer with it,
 * mid-redaction, the moment the save went through.
 */
export default function FileViewLink({ documentId, fileName, title, label = "View", onChanged }) {
  const [previewing, setPreviewing] = useState(false);
  // Whether the log changed while the viewer was open. A ref, not state: it
  // is read on close and never drawn.
  const changedRef = useRef(false);

  if (!documentId) {
    return (
      <span className="muted" title="No file is attached">
        No file
      </span>
    );
  }

  function closePreview() {
    setPreviewing(false);
    if (changedRef.current) {
      changedRef.current = false;
      if (onChanged) onChanged();
    }
  }

  return (
    <>
      <button type="button" className="link-button" onClick={() => setPreviewing(true)}>
        {label}
      </button>
      {previewing && (
        <DocumentPreview
          documentId={documentId}
          fileName={fileName}
          title={title}
          onClose={closePreview}
          onSavedToLog={() => {
            changedRef.current = true;
          }}
        />
      )}
    </>
  );
}

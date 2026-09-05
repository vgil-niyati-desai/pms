import { useState } from "react";
import DocumentPreview from "./DocumentPreview";

/**
 * The File cell in every list: opens the document in the in-app preview
 * rather than sending the browser off to download it.
 *
 * It owns the open/closed state so a list only has to render the cell, which
 * is why this is a component rather than a helper returning markup.
 */
export default function FileViewLink({ documentId, fileName, title, label = "View" }) {
  const [previewing, setPreviewing] = useState(false);

  if (!documentId) return "—";

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
          onClose={() => setPreviewing(false)}
        />
      )}
    </>
  );
}

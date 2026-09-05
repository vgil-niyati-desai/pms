import { useState } from "react";
import { Link } from "react-router-dom";
import DocumentForm from "./DocumentForm";
import DocumentList from "./DocumentList";
import { paths } from "../../app/routes";

/**
 * The original single-screen document log, unchanged in behaviour: the entry
 * form above the table of everything captured so far.
 *
 * It keeps working exactly as before while the Projects area is built, and
 * is reached from the Projects screen rather than the sidebar.
 */
export default function DocumentsPage() {
  const [refreshKey, setRefreshKey] = useState(0);
  // The entry currently loaded into the form for editing, or null for "add new".
  const [editingDocument, setEditingDocument] = useState(null);

  function handleSaved() {
    setRefreshKey((k) => k + 1);
  }

  function handleDeleted(deletedId) {
    // If the row being edited is the one that just got deleted, drop out of
    // edit mode so the form isn't pointing at a record that no longer exists.
    setEditingDocument((current) => (current && current.id === deletedId ? null : current));
  }

  return (
    <>
      <p className="muted page-note">
        The existing document log. Entries captured here stay available as the
        Projects area is built. <Link to={paths.projects()}>Back to Projects</Link>
      </p>
      <DocumentForm
        onSaved={handleSaved}
        editingDocument={editingDocument}
        onCancelEdit={() => setEditingDocument(null)}
      />
      <DocumentList
        refreshKey={refreshKey}
        editingId={editingDocument ? editingDocument.id : null}
        onEdit={setEditingDocument}
        onDeleted={handleDeleted}
      />
    </>
  );
}

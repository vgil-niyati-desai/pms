import { useState } from "react";
import { Link } from "react-router-dom";
import Drawer from "../../components/Drawer";
import DocumentForm from "./DocumentForm";
import DocumentList from "./DocumentList";
import { paths } from "../../app/routes";

/**
 * The document log: the table of everything captured so far, with the entry
 * form in a drawer over it rather than permanently above it.
 *
 * The form used to sit at the top of the page, which pushed the list -- the
 * thing the screen exists to show -- below the fold, and put the screen out
 * of step with every other list in the app. Adding and editing now open the
 * same drawer the CV, certification and attached-document lists use.
 */
export default function DocumentsPage() {
  const [refreshKey, setRefreshKey] = useState(0);
  // The entry loaded into the drawer, or null for "add new". Open is tracked
  // separately because null is itself a valid open state.
  const [editingDocument, setEditingDocument] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  function openCreate() {
    setEditingDocument(null);
    setDrawerOpen(true);
  }

  function openEdit(document) {
    setEditingDocument(document);
    setDrawerOpen(true);
  }

  function closeDrawer() {
    setDrawerOpen(false);
    setEditingDocument(null);
  }

  function handleSaved() {
    setRefreshKey((k) => k + 1);
    closeDrawer();
  }

  function handleDeleted(deletedId) {
    // The list is behind the drawer's backdrop while it is open, so this
    // normally cannot fire against the entry being edited. Kept so the
    // drawer can never be left pointing at a record that is gone.
    if (editingDocument && editingDocument.id === deletedId) closeDrawer();
  }

  return (
    <>
      <div className="list-head">
        <p className="muted page-note">
          The existing document log. Entries captured here stay available as the
          Projects area is built. <Link to={paths.projects()}>Back to Projects</Link>
        </p>
        <button type="button" className="btn btn-primary" onClick={openCreate}>
          + Add Document
        </button>
      </div>

      <DocumentList
        refreshKey={refreshKey}
        editingId={editingDocument ? editingDocument.id : null}
        onEdit={openEdit}
        onDeleted={handleDeleted}
      />

      {drawerOpen && (
        <Drawer title={editingDocument ? "Edit document" : "Add document"} onClose={closeDrawer}>
          <DocumentForm
            onSaved={handleSaved}
            editingDocument={editingDocument}
            onCancelEdit={closeDrawer}
          />
        </Drawer>
      )}
    </>
  );
}

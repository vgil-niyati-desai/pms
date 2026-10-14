import { useCallback, useState } from "react";
import Drawer from "../../components/Drawer";
import DocumentForm from "./DocumentForm";
import DocumentList from "./DocumentList";
import useHandoffNotice from "../../hooks/useHandoffNotice";

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
  // Set by this screen after an action it handled itself: the drawer closes
  // back onto this list, so there is nowhere to hand a message over to.
  const [notice, setNotice] = useHandoffNotice();

  // Reported by the form, which owns the fields; the drawer owns the ways out
  // of them, so it is told rather than asked. Stable, so the form's own effect
  // does not re-run on every render of this page.
  const [formDirty, setFormDirty] = useState(false);
  const reportDirty = useCallback((dirty) => setFormDirty(dirty), []);

  function openCreate() {
    setEditingDocument(null);
    setFormDirty(false);
    setDrawerOpen(true);
  }

  function openEdit(document) {
    setEditingDocument(document);
    setFormDirty(false);
    setDrawerOpen(true);
  }

  function closeDrawer() {
    setDrawerOpen(false);
    setEditingDocument(null);
    // The form it belonged to is gone, so nothing is unsaved any more. Without
    // this the next open would inherit the last one's answer until the form
    // reported again.
    setFormDirty(false);
  }

  function handleSaved() {
    // Read before closeDrawer clears it, since which of the two happened is
    // what the confirmation has to say.
    const wasEditing = Boolean(editingDocument);
    setRefreshKey((k) => k + 1);
    closeDrawer();
    // Nothing navigates here -- the drawer closes onto the list it saved to --
    // so the confirmation is set on this screen rather than handed over.
    setNotice(wasEditing ? "Document saved." : "Document added.");
  }

  function handleDeleted(deletedId) {
    // The list is behind the drawer's backdrop while it is open, so this
    // normally cannot fire against the entry being edited. Kept so the
    // drawer can never be left pointing at a record that is gone.
    if (editingDocument && editingDocument.id === deletedId) closeDrawer();
    // DocumentList calls this only once the delete has succeeded.
    setNotice("Document deleted.");
  }

  return (
    <>
      {notice && (
        <div className="alert alert-success notice-bar" role="status">
          <span>{notice}</span>
          <button
            type="button"
            className="notice-dismiss"
            onClick={() => setNotice(null)}
            aria-label="Dismiss this message"
          >
            &times;
          </button>
        </div>
      )}

      <DocumentList
        refreshKey={refreshKey}
        editingId={editingDocument ? editingDocument.id : null}
        onAdd={openCreate}
        onEdit={openEdit}
        onDeleted={handleDeleted}
      />

      {drawerOpen && (
        <Drawer
          title={editingDocument ? "Edit document" : "Add document"}
          onClose={closeDrawer}
          dirty={formDirty}
        >
          <DocumentForm
            onSaved={handleSaved}
            editingDocument={editingDocument}
            onCancelEdit={closeDrawer}
            onDirtyChange={reportDirty}
          />
        </Drawer>
      )}
    </>
  );
}

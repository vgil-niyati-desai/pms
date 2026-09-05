import Modal from "./Modal";

/**
 * "Are you sure?" dialog for destructive actions.
 *
 * `children` describes what is about to be removed; the caller owns the
 * delete itself and reports progress through `busy` and `error`.
 */
export default function ConfirmDelete({
  title = "Delete this entry?",
  confirmLabel = "Delete entry",
  busyLabel = "Deleting...",
  busy = false,
  error = null,
  onConfirm,
  onCancel,
  children,
}) {
  return (
    <Modal title={title} onClose={onCancel} dismissable={!busy}>
      {children}
      {error && <div className="alert alert-error">{error}</div>}
      <div className="form-actions">
        <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
          {busy ? busyLabel : confirmLabel}
        </button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

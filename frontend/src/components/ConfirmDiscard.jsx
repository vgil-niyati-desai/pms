import Modal from "./Modal";

/**
 * "You have unsaved changes" dialog, for a form being closed or left with
 * something in it that has not been saved.
 *
 * The counterpart to ConfirmDelete, and deliberately the other way round in
 * one respect: there the destructive answer is the one being asked for, so it
 * leads. Here the person has not asked to lose anything -- they hit Escape,
 * or clicked the backdrop, or reached for Cancel -- so the safe answer is the
 * prominent one, and every other way out of this dialog (Escape, its own
 * backdrop) also keeps the changes. Discarding takes a deliberate click on
 * the button that says so.
 *
 * Rendered by whatever layer is being closed rather than at the top of the
 * app: a Modal sits at z-index 10 and a drawer's backdrop at 20, so over a
 * drawer this has to be a child of that backdrop to paint above it -- the
 * same arrangement the document preview uses for its own discard dialog.
 */
export default function ConfirmDiscard({
  title = "Discard unsaved changes?",
  body = "This form has changes that have not been saved. Closing it now loses them.",
  discardLabel = "Discard changes",
  keepLabel = "Keep editing",
  onDiscard,
  onKeep,
}) {
  return (
    <Modal title={title} onClose={onKeep}>
      <p className="muted">{body}</p>
      <div className="form-actions">
        <button type="button" className="btn btn-primary" onClick={onKeep}>
          {keepLabel}
        </button>
        <button type="button" className="btn btn-danger" onClick={onDiscard}>
          {discardLabel}
        </button>
      </div>
    </Modal>
  );
}

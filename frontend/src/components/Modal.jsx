import { useId, useRef } from "react";
import useDialogFocus from "../hooks/useDialogFocus";
import useEscapeKey from "../hooks/useEscapeKey";

/**
 * Centred dialog over a dimmed backdrop.
 *
 * Generalised from the delete-confirmation dialog in DocumentList: same
 * markup and the same two dismiss routes (backdrop click, Escape), with
 * `dismissable` standing in for the "not while deleting" guard.
 */
export default function Modal({ title, onClose, dismissable = true, children }) {
  const titleId = useId();
  const panelRef = useRef(null);

  function requestClose() {
    if (dismissable) onClose();
  }

  useEscapeKey(dismissable, requestClose);

  // The whole dialog is the content here, so there is no inner part to prefer:
  // a confirmation opens on its least destructive answer and returns focus to
  // whatever asked the question.
  useDialogFocus(panelRef);

  return (
    <div className="modal-backdrop" onClick={requestClose}>
      <div
        ref={panelRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

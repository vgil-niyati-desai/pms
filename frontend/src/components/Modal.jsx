import { useId } from "react";
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

  function requestClose() {
    if (dismissable) onClose();
  }

  useEscapeKey(dismissable, requestClose);

  return (
    <div className="modal-backdrop" onClick={requestClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

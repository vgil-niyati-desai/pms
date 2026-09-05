import { useId } from "react";
import useEscapeKey from "../hooks/useEscapeKey";

/**
 * Panel that slides in over the current screen, so opening a record or a
 * menu doesn't lose the list underneath. Dismissed by the close button, the
 * backdrop, or Escape.
 *
 * `side` is "right" for record drawers and "left" for the mobile navigation.
 */
export default function Drawer({ title, onClose, side = "right", dismissable = true, children }) {
  const titleId = useId();

  function requestClose() {
    if (dismissable) onClose();
  }

  useEscapeKey(dismissable, requestClose);

  return (
    <div className="drawer-backdrop" onClick={requestClose}>
      <aside
        className={`drawer drawer-${side}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drawer-head">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="drawer-close"
            onClick={requestClose}
            disabled={!dismissable}
            aria-label="Close"
          >
            &times;
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

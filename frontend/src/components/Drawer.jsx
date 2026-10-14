import { createContext, useContext, useId, useRef, useState } from "react";
import ConfirmDiscard from "./ConfirmDiscard";
import useDialogFocus from "../hooks/useDialogFocus";
import useEscapeKey from "../hooks/useEscapeKey";

const RequestCloseContext = createContext(null);

/**
 * The drawer's own guarded close, for a form inside it that offers a way out
 * of its own -- a Cancel button. Going through this means Cancel asks about
 * unsaved changes exactly as the close button, the backdrop and Escape do,
 * instead of being the one route that drops them silently.
 *
 * null outside a Drawer, so a form that also renders elsewhere can fall back
 * to whatever close it was handed.
 */
export function useRequestDrawerClose() {
  return useContext(RequestCloseContext);
}

/**
 * A form's Cancel button, closing through the drawer's guarded close.
 *
 * A component of its own because the hook above only answers from inside
 * the drawer: a drawer component that renders <Drawer> itself sits outside
 * the provider, and calling the hook there gets null. Rendered among the
 * drawer's children, this always finds it.
 */
export function DrawerCancelButton({ disabled = false, children = "Cancel" }) {
  const requestClose = useRequestDrawerClose();
  return (
    <button type="button" className="btn" onClick={requestClose} disabled={disabled}>
      {children}
    </button>
  );
}

/**
 * Panel that slides in over the current screen, so opening a record or a
 * menu doesn't lose the list underneath. Dismissed by the close button, the
 * backdrop, or Escape.
 *
 * `side` is "right" for record drawers and "left" for the mobile navigation.
 *
 * `dirty` says the panel holds unsaved changes. All three dismiss routes then
 * ask before closing, rather than each drawer having to guard its own -- they
 * all lead to the same `onClose`, and this is where that lives. Left out, a
 * drawer behaves exactly as it did: the navigation drawer and the read-only
 * ones pass nothing.
 *
 * `dismissable` still wins over `dirty`. A drawer mid-save is not asking a
 * question it could act on, so it stays put and says nothing.
 */
export default function Drawer({
  title,
  onClose,
  side = "right",
  dismissable = true,
  dirty = false,
  children,
}) {
  const titleId = useId();
  const [confirming, setConfirming] = useState(false);
  // The backdrop, the panel inside it, and the panel's body: the body is
  // where focus should land, and the backdrop is the whole layer -- it holds
  // the discard confirmation as well as the panel.
  const layerRef = useRef(null);
  const panelRef = useRef(null);
  const bodyRef = useRef(null);

  function requestClose() {
    if (!dismissable) return;
    if (dirty) {
      setConfirming(true);
      return;
    }
    onClose();
  }

  // Only while the confirmation is not up. Once it is, it is the top layer
  // and Escape belongs to it -- useEscapeKey hands the key to the most
  // recently opened layer, so it cancels the question rather than answering
  // it by closing the drawer underneath.
  useEscapeKey(dismissable && !confirming, requestClose);

  // Opens on the first field of whatever is inside, and hands focus back to
  // the button that opened it on the way out.
  useDialogFocus(panelRef, bodyRef, layerRef);

  return (
    <div className="drawer-backdrop" ref={layerRef} onClick={requestClose}>
      <aside
        ref={panelRef}
        className={`drawer drawer-${side}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        // Focusable only on purpose, for the panel that has nothing inside it
        // to open on, and never a stop on the way round with Tab.
        tabIndex={-1}
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
        <div className="drawer-body" ref={bodyRef}>
          <RequestCloseContext.Provider value={requestClose}>
            {children}
          </RequestCloseContext.Provider>
        </div>
      </aside>

      {confirming && (
        // Inside this backdrop, not beside it: a Modal is z-index 10 and this
        // backdrop is 20, so as a sibling the question would be painted
        // behind the drawer it is about. Its own clicks must not reach the
        // backdrop's handler either, which would read as a second request to
        // close.
        <div onClick={(e) => e.stopPropagation()}>
          <ConfirmDiscard
            onKeep={() => setConfirming(false)}
            onDiscard={() => {
              setConfirming(false);
              onClose();
            }}
          />
        </div>
      )}
    </div>
  );
}

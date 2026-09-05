import { useEffect } from "react";

/**
 * Calls `onEscape` when Escape is pressed, while `active` is true.
 *
 * Extracted from the delete-confirmation dialog, which used this to let
 * Escape dismiss the dialog but not while the delete was already in flight —
 * hence `active` rather than an always-on listener.
 */
export default function useEscapeKey(active, onEscape) {
  useEffect(() => {
    if (!active) return undefined;
    function onKeyDown(e) {
      if (e.key === "Escape") onEscape();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, onEscape]);
}

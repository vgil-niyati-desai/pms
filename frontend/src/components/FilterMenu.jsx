import { useEffect, useLayoutEffect, useRef, useState } from "react";
import useEscapeKey from "../hooks/useEscapeKey";

/**
 * A filter too tall or too wide for a line of the toolbar — a set of
 * checkboxes, a from/to pair, the tag box — folded behind a trigger.
 *
 * The trigger carries the filter's name and how many values it currently
 * holds, so the row still says what is narrowing the list without any of
 * these filters being open. Dismissed by Escape or a click outside.
 */
export default function FilterMenu({ label, count = 0, children }) {
  const [open, setOpen] = useState(false);
  // The panel hangs off the trigger's left edge, except where that would run
  // it off the screen — then off the right edge instead.
  const [alignEnd, setAlignEnd] = useState(false);
  const rootRef = useRef(null);
  const panelRef = useRef(null);

  useEscapeKey(open, () => setOpen(false));

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event) {
      if (!rootRef.current.contains(event.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  // Measured before paint, and only while opening — `alignEnd` is deliberately
  // not a dependency, so the flipped panel is not measured again and flipped
  // back on the next pass.
  useLayoutEffect(() => {
    if (!open) {
      setAlignEnd(false);
      return;
    }
    const room = document.documentElement.clientWidth - 8;
    if (panelRef.current.getBoundingClientRect().right > room) setAlignEnd(true);
  }, [open]);

  return (
    <div className="filter-menu" ref={rootRef}>
      <button
        type="button"
        className={count ? "btn filter-menu-trigger filter-menu-set" : "btn filter-menu-trigger"}
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        {label}
        {count > 0 && (
          <>
            <span className="filter-menu-count" aria-hidden="true">
              {count}
            </span>
            {/* The badge alone would read as "Has document5". */}
            <span className="sr-only">{count} selected</span>
          </>
        )}
        <span className="filter-menu-caret" aria-hidden="true">
          ▾
        </span>
      </button>

      {open && (
        <div
          ref={panelRef}
          className={alignEnd ? "filter-menu-panel filter-menu-panel-end" : "filter-menu-panel"}
        >
          {children}
        </div>
      )}
    </div>
  );
}

import { useLayoutEffect, useRef, useState } from "react";

/**
 * Calls `onEscape` when Escape is pressed, while `active` is true.
 *
 * Extracted from the delete-confirmation dialog, which used this to let
 * Escape dismiss the dialog but not while the delete was already in flight —
 * hence `active` rather than an always-on listener.
 *
 * Layers stack — a confirmation over a drawer, a preview over a drawer — and
 * one Escape must only close the one on top. Each caller used to add its own
 * window listener, so a single press closed every open layer at once. Now all
 * of them share one listener, and only the most recently opened active layer
 * hears the key. Opening order is fixed at a layer's first render, so a layer
 * that goes briefly inactive (a drawer mid-save) keeps its place in the stack.
 */
let nextOrder = 0;
const layers = new Set();

function onKeyDown(e) {
  if (e.key !== "Escape") return;
  let top = null;
  for (const layer of layers) {
    if (!top || layer.order > top.order) top = layer;
  }
  if (top) top.handlerRef.current();
}

export default function useEscapeKey(active, onEscape) {
  const [order] = useState(() => ++nextOrder);
  const handlerRef = useRef(onEscape);

  // Refreshed before the browser can deliver another key press, so the
  // handler Escape reaches always sees the state of the latest render.
  useLayoutEffect(() => {
    handlerRef.current = onEscape;
  });

  // A layout effect as well: a dialog that has just opened is the top layer
  // from the commit that shows it, not from some later tick.
  useLayoutEffect(() => {
    if (!active) return undefined;
    const layer = { order, handlerRef };
    if (layers.size === 0) window.addEventListener("keydown", onKeyDown);
    layers.add(layer);
    return () => {
      layers.delete(layer);
      if (layers.size === 0) window.removeEventListener("keydown", onKeyDown);
    };
  }, [active, order]);
}

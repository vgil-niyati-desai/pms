import { useEffect } from "react";

// Everything the browser can put keyboard focus on, as these screens use it.
// `[tabindex]` is here for completeness; the panels themselves carry -1 and
// are filtered out below.
const FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";

/**
 * The element a dialog should open on, searched in document order.
 *
 * Two things are skipped. Anything the browser would not focus anyway --
 * disabled, hidden, or taken out of the tab order -- and anything destructive.
 * ConfirmDelete deliberately leads with its Delete button, because that is the
 * answer being asked for; opening with focus already on it would turn the next
 * Enter into the delete itself. Skipping `btn-danger` means that dialog opens
 * on Cancel and the drawers open on their first field, which is what each one
 * wants, without either having to say so.
 */
function openingFocus(root) {
  if (!root) return null;
  for (const el of root.querySelectorAll(FOCUSABLE)) {
    if (el.disabled || el.type === "hidden") continue;
    if (el.getAttribute("tabindex") === "-1") continue;
    if (el.classList.contains("btn-danger")) continue;
    // Laid out: an element inside a section that is collapsed or not shown
    // yet cannot take focus, and asking it to would drop focus on the body.
    if (!el.getClientRects().length) continue;
    return el;
  }
  return null;
}

/**
 * Keyboard focus for a dialog that mounts when it opens and unmounts when it
 * closes -- which is how every Drawer and Modal here works, since what is open
 * is read from the query string and rendered or not.
 *
 * On open, focus moves into the panel: the first field of the form inside it,
 * or failing that whatever the panel itself offers (the close button), or the
 * panel. Without this, focus stayed on the row button that opened the drawer,
 * so Tab walked the list underneath a panel that claims to be modal.
 *
 * On close, focus goes back to the element that opened it, so the keyboard
 * lands where it left off instead of at the top of the document. Two cases it
 * stays out of:
 *
 *   * Focus has moved on to something outside this layer. A drawer and the
 *     discard confirmation over it close in one commit, and React runs the
 *     outer cleanup first: the drawer puts focus on the row that opened it,
 *     and the confirmation then finds focus somewhere that is not its own and
 *     leaves it alone, rather than dragging it to a Cancel button that is
 *     about to be removed.
 *   * The opener is gone from the document, or there never was one (a drawer
 *     opened straight from a URL, a row deleted by the dialog that asked about
 *     it). Focusing a detached node or the body is how focus gets lost, not
 *     restored.
 *
 * `contentRef` is the part of the panel to look in first, so a drawer opens on
 * its form rather than on the close button in its header.
 *
 * `layerRef` is everything this dialog puts on screen, for the check above. It
 * defaults to the panel and is only separate for the Drawer, which renders its
 * confirmation as a sibling of the panel rather than inside it -- the only way
 * to paint it over a drawer, and so the one case where "focus is still in
 * here" means something wider than the panel.
 */
export default function useDialogFocus(panelRef, contentRef = null, layerRef = null) {
  // Mount and unmount only. Re-running on a re-render would pull focus back to
  // the first field while someone is typing in the third.
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return undefined;
    const layer = (layerRef && layerRef.current) || panel;

    const opener = document.activeElement;
    const target = openingFocus(contentRef && contentRef.current) || openingFocus(panel) || panel;
    target.focus();

    return () => {
      const active = document.activeElement;
      const stillOurs = !active || active === document.body || layer.contains(active);
      if (!stillOurs) return;
      if (!opener || opener === document.body) return;
      if (!opener.isConnected || typeof opener.focus !== "function") return;
      opener.focus();
    };

  }, []);
}

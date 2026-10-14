import { useCallback } from "react";
import { useBlocker } from "react-router-dom";
import ConfirmDiscard from "../components/ConfirmDiscard";
import { shouldBlockNavigation } from "../lib/unsavedChanges";
import { useDirtyRegistry } from "./UnsavedChangesContext";

/**
 * Asks before a route change leaves unsaved changes behind.
 *
 * The drawers and the form pages already guard their own ways out -- close,
 * backdrop, Escape, Cancel -- and the browser asks on a reload. This covers
 * the rest: the sidebar, the topbar's "+ New" button, the mobile menu, and
 * the browser's own Back and Forward. All of those go through the router,
 * and useBlocker is the router's one hook for stopping them, so this is the
 * one place they are stopped. It needs a data router, which is why App
 * builds one.
 *
 * Rendered inside the drawer-confirm layer: a Modal paints at z-index 10 and
 * a drawer's backdrop at 20, so without it the question about a dirty drawer
 * would sit behind the drawer it is about.
 */
export default function NavigationGuard() {
  const registry = useDirtyRegistry();
  const shouldBlock = useCallback(
    ({ currentLocation, nextLocation }) =>
      shouldBlockNavigation(registry.anyDirty(), currentLocation, nextLocation),
    [registry],
  );
  const blocker = useBlocker(shouldBlock);

  if (blocker.state !== "blocked") return null;
  return (
    <div className="drawer-confirm-layer">
      <ConfirmDiscard
        body="This screen has changes that have not been saved. Leaving now loses them."
        onKeep={() => blocker.reset()}
        onDiscard={() => blocker.proceed()}
      />
    </div>
  );
}

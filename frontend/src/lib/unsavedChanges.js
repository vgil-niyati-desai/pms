/**
 * The pure parts of the app-wide unsaved-changes guard, kept free of React so
 * they can be tested on their own. The React side -- the provider, the hook
 * that registers a form's dirty flag, and the dialog -- lives in
 * app/UnsavedChangesContext.jsx and app/NavigationGuard.jsx.
 */

/**
 * The set of surfaces that currently hold unsaved changes.
 *
 * More than one can be dirty at once -- a redaction in progress over a drawer
 * whose form has been typed into -- and the router allows exactly one
 * navigation blocker, so the blocker asks this rather than any one form. Ids
 * are opaque; a surface adds its own while dirty and removes it when clean or
 * gone, and adding or removing twice is harmless.
 */
export function createDirtyRegistry() {
  const ids = new Set();
  return {
    add(id) {
      ids.add(id);
    },
    remove(id) {
      ids.delete(id);
    },
    anyDirty() {
      return ids.size > 0;
    },
  };
}

/**
 * Whether a navigation should stop and ask first.
 *
 * Only a change of pathname is a way out of a screen. The query string is
 * where the detail pages keep which tab and which drawer are open, and every
 * drawer closes itself after a save by rewriting it -- at an instant when its
 * form still differs from the record it opened on. Those pass. The drawers
 * already guard their own close routes, so nothing is lost by letting them.
 */
export function shouldBlockNavigation(anyDirty, currentLocation, nextLocation) {
  if (!anyDirty) return false;
  return currentLocation.pathname !== nextLocation.pathname;
}

import { createContext, useCallback, useContext, useId, useLayoutEffect, useMemo } from "react";
import { createDirtyRegistry } from "../lib/unsavedChanges";

const RegistryContext = createContext(null);

/**
 * Holds the registry of surfaces with unsaved changes for everything under
 * it. Mounted once, by AppShell, so a form page, a drawer on a detail page
 * and the redactor over either all report to the same place -- and the one
 * navigation blocker in NavigationGuard can read it.
 */
export function UnsavedChangesProvider({ children }) {
  const registry = useMemo(() => createDirtyRegistry(), []);
  return <RegistryContext.Provider value={registry}>{children}</RegistryContext.Provider>;
}

/** The registry, or null outside the provider (as in a unit test). */
export function useDirtyRegistry() {
  return useContext(RegistryContext);
}

/**
 * Keeps this component in the registry for as long as `dirty` is true.
 *
 * A layout effect, for the same reason the drawers report dirtiness in one:
 * a route change requested straight after a keystroke -- Back on the
 * keyboard, say -- must already find the form registered.
 *
 * Returns `release`, which takes the component out of the registry at once,
 * without waiting for a render. A form that has just saved or deleted, or
 * whose discard has just been confirmed, calls it before navigating away:
 * the router checks the registry synchronously inside navigate, so a state
 * change alone would arrive too late and the form's own navigation would be
 * the thing that got blocked.
 */
export function useRegisterDirty(dirty) {
  const registry = useContext(RegistryContext);
  const id = useId();

  useLayoutEffect(() => {
    if (!registry || !dirty) return undefined;
    registry.add(id);
    return () => registry.remove(id);
  }, [registry, dirty, id]);

  return useCallback(() => {
    if (registry) registry.remove(id);
  }, [registry, id]);
}

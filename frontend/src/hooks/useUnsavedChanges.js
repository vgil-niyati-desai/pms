import { useCallback, useEffect, useRef, useState } from "react";
import { useRegisterDirty } from "../app/UnsavedChangesContext";

/**
 * Whether an editable form holds changes that have not been saved.
 *
 * `snapshot` is whatever the form would lose: pass the same plain object on
 * every render and this compares it against the one it was first given. A
 * form is dirty when the two differ, so typing a value and typing it back
 * again leaves nothing to warn about -- which is the point. Nobody should be
 * asked to confirm discarding a change they have already undone.
 *
 * Compared by JSON, which is exact enough for what these forms hold (strings,
 * numbers, booleans, string lists) and needs no per-form comparison code.
 * Two things it cannot see, so callers put them in the snapshot themselves:
 *
 *   * A File from an <input type="file"> serialises to {}. Pass its name --
 *     `file ? file.name : ""` -- which is what changes when one is picked.
 *   * Key order matters to JSON. Every form here builds its state from one
 *     blank-or-record helper and updates it by spreading, so the order is
 *     stable; a snapshot assembled fresh each render must keep its keys in a
 *     fixed order too.
 *
 * `markSaved` moves the baseline to what is on screen now, for a form that
 * stays open after saving. A form that closes or navigates away on save does
 * not need it: it unmounts, and the effect below goes with it.
 *
 * `pristine`, when given, is used as the baseline instead of capturing one --
 * see the note on it below.
 *
 * While dirty, this also asks the browser to confirm a reload, a tab close or
 * a link out of the app -- the one exit no in-page dialog can intercept. The
 * browser shows its own wording; the message is not ours to choose.
 *
 * And while dirty, the form is registered with NavigationGuard, which asks
 * before a route change -- the sidebar, the topbar's "+ New", the browser's
 * Back -- leaves it behind. `release` takes it out of that at once, for a
 * form that is about to navigate away on purpose: after a save or a delete
 * has gone through, or once its own discard dialog has been answered. Those
 * navigations happen while the snapshot still differs from the baseline, and
 * the guard checks synchronously, so this has to be a call and not a render.
 * Typing again after a release makes the form dirty again.
 */
export default function useUnsavedChanges(snapshot, pristine) {
  const serialised = JSON.stringify(snapshot ?? null);

  // The baseline is normally captured on the first render, which suits a form
  // whose state is built from the record it is editing before that render --
  // every drawer here does, through a useState initialiser.
  //
  // `pristine` is for the one form that cannot: DocumentForm loads the record
  // into state from an effect, so its first render still holds a blank form
  // and a captured baseline would read the loaded record as a change someone
  // made. Passing what the saved record *should* look like derives the
  // baseline instead of capturing it, so it is right from the first render and
  // stays right when the form is pointed at a different record.
  const derived = pristine !== undefined;
  const [captured, setCaptured] = useState(serialised);

  // Written on every render so markSaved, which is stable, still sees the
  // latest snapshot when it is called.
  const latest = useRef(serialised);
  latest.current = serialised;

  const baseline = derived ? JSON.stringify(pristine ?? null) : captured;
  // What the form held when release() was last called, or null. A snapshot
  // equal to it is not dirty: the person is leaving with it on purpose.
  const [releasedAt, setReleasedAt] = useState(null);
  const dirty = serialised !== baseline && serialised !== releasedAt;

  // Nothing to move when the baseline is derived: it already follows the
  // record, so a save that updates the record updates this with it.
  const markSaved = useCallback(() => setCaptured(latest.current), []);

  // Out of the guard's registry now, and clean on the next render too, so
  // the beforeunload handler below goes with it.
  const unregister = useRegisterDirty(dirty);
  const release = useCallback(() => {
    unregister();
    setReleasedAt(latest.current);
  }, [unregister]);

  useEffect(() => {
    if (!dirty) return undefined;
    function confirmUnload(event) {
      event.preventDefault();
      // Set as well as prevented: older browsers read this instead.
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", confirmUnload);
    return () => window.removeEventListener("beforeunload", confirmUnload);
  }, [dirty]);

  return { dirty, markSaved, release };
}

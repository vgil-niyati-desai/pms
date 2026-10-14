import { useCallback, useState } from "react";

// Namespaced, since localStorage is shared with everything else served from
// this origin.
const STORAGE_KEY = "pms.document.addedBy";

function read() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    // Private windows and blocked site data throw on access rather than
    // returning nothing. Nobody's name is worth a broken form.
    return "";
  }
}

/**
 * The name last entered in a document's "Added by", kept for the next new one.
 *
 * It is the same person filing document after document, typing their own name
 * into every one, so the field is prefilled from the last entry rather than
 * asked for again. Prefilled, not fixed: it is an ordinary input and whatever
 * is in it on the next save is what gets remembered instead.
 *
 * Why localStorage rather than state held higher up: the two forms that have
 * this field are mounted and unmounted by the query string, so there is no
 * component alive long enough to hold the value, and a refresh would lose it
 * from anywhere else in the app. It is a preference about this browser, which
 * is what localStorage is for, and it is read through a guard because reading
 * it is allowed to fail.
 *
 * Only a *created* record feeds this. Editing one is usually correcting
 * somebody else's entry, and taking the name off it would quietly make that
 * person the default for everything filed next.
 *
 * `remember` returns the value it stored, trimmed, so a caller that resets its
 * form in the same breath can fill the field with exactly what the baseline
 * for unsaved changes now holds.
 */
export default function useRememberedAddedBy() {
  // Read once, on the render that builds the blank form. A value that arrived
  // later would appear in a field somebody may already be typing in.
  const [remembered, setRemembered] = useState(read);

  const remember = useCallback((value) => {
    const name = (value || "").trim();
    setRemembered(name);
    try {
      if (name) window.localStorage.setItem(STORAGE_KEY, name);
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Storage unavailable or full: the record still saved, this just will
      // not carry over to the next one.
    }
    return name;
  }, []);

  return [remembered, remember];
}

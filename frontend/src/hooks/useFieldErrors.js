import { useCallback, useState } from "react";

/**
 * Per-field validation messages for a form, keyed by the control's id.
 *
 * `report(problems)` takes the messages in the order the fields appear on
 * screen, shows them, and moves focus to the first one so the person lands on
 * what needs fixing. It returns whether there was anything to report, so a
 * submit handler can `if (report(problems)) return;`.
 *
 * `set(id, message)` flags one field as it is being edited -- a file picked of
 * the wrong type, say -- without moving focus or touching the others.
 *
 * `clear(id)` drops one message as soon as that field is edited, and `reset()`
 * drops them all -- before a fresh submit, or when the form starts over.
 * `summary` is the line for the alert above the form, or null when nothing
 * is outstanding.
 */
export default function useFieldErrors() {
  const [errors, setErrors] = useState({});

  const clear = useCallback((id) => {
    setErrors((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const set = useCallback((id, message) => {
    setErrors((prev) => ({ ...prev, [id]: message }));
  }, []);

  const reset = useCallback(() => setErrors({}), []);

  const report = useCallback((problems) => {
    setErrors(problems);
    const first = Object.keys(problems)[0];
    if (!first) return false;
    document.getElementById(first)?.focus();
    return true;
  }, []);

  // The alert above the form, worked out from what is still wrong so it
  // counts down as fields are fixed and goes once the last one is.
  const count = Object.keys(errors).length;
  const summary =
    count === 0
      ? null
      : count === 1
        ? "Please fix the highlighted field below."
        : `Please fix the ${count} highlighted fields below.`;

  return { errors, summary, report, set, clear, reset };
}

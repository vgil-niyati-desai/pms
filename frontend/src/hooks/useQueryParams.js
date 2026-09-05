import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Filter state held in the URL rather than in component state, so a filtered
 * list can be bookmarked and shared, and Back steps through filter changes.
 *
 * `schema` gives every key a default; the default's type decides how the
 * value is parsed — number, array (comma-separated), or string.
 */
export default function useQueryParams(schema) {
  const [searchParams, setSearchParams] = useSearchParams();

  const values = useMemo(() => {
    const parsed = {};
    for (const [key, fallback] of Object.entries(schema)) {
      const raw = searchParams.get(key);
      if (raw === null || raw === "") {
        parsed[key] = fallback;
      } else if (Array.isArray(fallback)) {
        parsed[key] = raw.split(",").filter(Boolean);
      } else if (typeof fallback === "number") {
        const asNumber = Number(raw);
        parsed[key] = Number.isFinite(asNumber) ? asNumber : fallback;
      } else {
        parsed[key] = raw;
      }
    }
    return parsed;
    // searchParams is a fresh object each render; its string form is what matters.
  }, [searchParams.toString(), schema]); // eslint-disable-line react-hooks/exhaustive-deps

  const setValues = useCallback(
    (patch) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(patch)) {
            const isDefault =
              Array.isArray(schema[key]) && Array.isArray(value)
                ? value.length === 0
                : value === schema[key] || value === "" || value === undefined || value === null;
            // A value equal to its default is left out, keeping URLs short.
            if (isDefault) next.delete(key);
            else next.set(key, Array.isArray(value) ? value.join(",") : String(value));
          }
          return next;
        },
        { replace: true }
      );
    },
    [setSearchParams, schema]
  );

  const clear = useCallback(() => setSearchParams({}, { replace: true }), [setSearchParams]);

  return { values, setValues, clear };
}

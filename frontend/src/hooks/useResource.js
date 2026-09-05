import { useCallback, useEffect, useState } from "react";

/**
 * Runs an async loader and tracks its data, loading and error state.
 *
 * `loader` must be memoised by the caller (useCallback), since it is the
 * dependency that decides when to refetch. Results from a superseded call are
 * discarded, so a slow response can't overwrite a newer one.
 */
export default function useResource(loader, { initialData = null } = {}) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(null);
    loader()
      .then((result) => {
        if (current) setData(result);
      })
      .catch((err) => {
        if (current) setError(err.message || "Something went wrong.");
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [loader, reloadKey]);

  return { data, loading, error, reload, setData };
}

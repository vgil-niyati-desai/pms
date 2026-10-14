/**
 * Runs the Dashboard's summary-count requests so that one failure cannot
 * discard the others. `loaders` maps a tile key to an async function that
 * resolves to a list response carrying `total`.
 *
 * Every key comes back as either `{ value }` or `{ error }`, so a failed
 * request is never mistaken for a count of zero.
 */
export async function settleStats(loaders) {
  const keys = Object.keys(loaders);
  const settled = await Promise.allSettled(keys.map((key) => loaders[key]()));
  const result = {};
  keys.forEach((key, index) => {
    const outcome = settled[index];
    result[key] =
      outcome.status === "fulfilled"
        ? { value: outcome.value.total }
        : { error: outcome.reason?.message || "Something went wrong." };
  });
  return result;
}

/** The distinct messages of the failed tiles, for the single banner. */
export function statsErrors(stats) {
  if (!stats) return [];
  return [...new Set(Object.values(stats).filter((s) => s.error).map((s) => s.error))];
}

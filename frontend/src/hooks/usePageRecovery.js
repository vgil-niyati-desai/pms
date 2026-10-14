import { useEffect } from "react";

/** The furthest page a list may ask for. Must match MAX_PAGE in the backend routers. */
export const MAX_PAGE = 100000;

/**
 * A page number from the URL as one the server accepts: a whole number from
 * 1 to MAX_PAGE. `?page=0` or a negative becomes 1, `?page=2.5` becomes 2.
 * useQueryParams already turns anything that is not a number into the
 * default, so this only has to tighten a number.
 */
export function wholePage(value) {
  const page = Math.trunc(Number(value));
  if (!Number.isFinite(page) || page < 1) return 1;
  return Math.min(page, MAX_PAGE);
}

/**
 * Keeps a paged list on a page that exists.
 *
 * Two ways a URL can name a page that is not there: a malformed number
 * (`rawPage`, as the URL has it, differs from `page`, the whole number the
 * list actually asked for), and a page past the end -- a stale bookmark, or
 * rows deleted since. Either way the URL is moved to the page that is shown:
 * the cleaned number, or the last page that has rows (page 1 when nothing
 * matches at all).
 *
 * "Past the end" is only judged from the answer to this very page
 * (`data.page === page`), never from the previous page's answer still on
 * screen while the next one loads.
 *
 * Returns true while the page is being moved, so the list can show its
 * loading state instead of an empty table claiming there is nothing there.
 */
export default function usePageRecovery({ rawPage, page, resource, pageSize, setPage }) {
  const { data, loading, error } = resource;
  const total = data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const malformed = rawPage !== page;
  const pastEnd = !loading && !error && data?.page === page && page > lastPage;

  useEffect(() => {
    if (malformed) setPage(page);
    else if (pastEnd) setPage(lastPage);
  }, [malformed, pastEnd, page, lastPage, setPage]);

  return malformed || pastEnd;
}

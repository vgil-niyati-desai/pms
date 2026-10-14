/** Previous/next paging. Renders nothing when everything fits on one page. */
export default function Pagination({ page, pageSize, total, onPageChange }) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;

  // The range is worked out from a page that exists, so a page past the end
  // -- shown only until the list moves back to the last one -- can never
  // read as "981–29 of 29".
  const shown = Math.min(Math.max(1, page), lastPage);
  const first = (shown - 1) * pageSize + 1;
  const last = Math.min(shown * pageSize, total);

  return (
    <div className="pagination">
      <span className="muted">
        {first}&ndash;{last} of {total}
      </span>
      <button type="button" className="btn btn-sm" onClick={() => onPageChange(shown - 1)} disabled={shown <= 1}>
        Previous
      </button>
      <button type="button" className="btn btn-sm" onClick={() => onPageChange(shown + 1)} disabled={shown >= lastPage}>
        Next
      </button>
    </div>
  );
}

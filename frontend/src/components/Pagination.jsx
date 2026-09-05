/** Previous/next paging. Renders nothing when everything fits on one page. */
export default function Pagination({ page, pageSize, total, onPageChange }) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;

  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="pagination">
      <span className="muted">
        {first}&ndash;{last} of {total}
      </span>
      <button type="button" className="btn btn-sm" onClick={() => onPageChange(page - 1)} disabled={page <= 1}>
        Previous
      </button>
      <button type="button" className="btn btn-sm" onClick={() => onPageChange(page + 1)} disabled={page >= lastPage}>
        Next
      </button>
    </div>
  );
}

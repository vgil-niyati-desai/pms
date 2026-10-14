import { TableSkeleton } from "./Skeleton";

/**
 * Table driven by a column config, with the loading, error and empty states
 * the list screens all need -- shown one at a time, in that order of
 * precedence. `error` is the failed load's message and `onRetry` reloads it;
 * a table given neither behaves exactly as before.
 *
 * A column is `{ key, header, render?, className? }`. `render(row)` takes
 * over the cell when a value needs a link, a pill, or buttons; otherwise the
 * raw `row[key]` is shown.
 *
 * `onRowClick(row)` makes the whole row open the record. The list screens use
 * it instead of a link on the name, so the name reads as the table text it is
 * and the target is the row rather than a few words inside it.
 */

/* Cells carry their own links and buttons — View, tag removal, row actions.
   A click that lands on one of those is meant for it, not for the row. */
const INTERACTIVE = "a, button, input, select, textarea, label, [role='button']";

export default function DataTable({
  columns,
  rows,
  rowKey = (row) => row.id,
  rowClassName,
  onRowClick,
  loading = false,
  loadingMessage = "Loading...",
  skeletonRows = 5,
  empty = null,
  error = null,
  onRetry,
}) {
  // The table's own shape rather than a line of text where a table was: the
  // headers and the box stay put and the cells fill in. `loadingMessage` is
  // what a screen reader is told, since the placeholder itself is for the eye.
  if (loading) {
    return <TableSkeleton columns={columns} rows={skeletonRows} message={loadingMessage} />;
  }
  // A failed load is not an empty result. Shown in place of both the rows --
  // which would be the previous request's, not this one's -- and the empty
  // state, whose "No projects yet" would claim something nobody knows.
  if (error) {
    return (
      <div className="alert alert-error table-error" role="alert">
        <span>{error}</span>
        {onRetry && (
          <button type="button" className="btn btn-sm" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    );
  }
  if (!rows.length) return empty;

  function handleRowClick(event, row) {
    if (event.target.closest(INTERACTIVE)) return;
    // Releasing the mouse at the end of a text selection is not a click on
    // the row, and navigating away from what you just highlighted is rude.
    if (window.getSelection()?.toString()) return;
    onRowClick(row);
  }

  function handleRowKeyDown(event, row) {
    // Only the row itself; Enter and Space inside a cell's control are that
    // control's to handle.
    if (event.target !== event.currentTarget) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onRowClick(row);
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.className}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const className =
              [rowClassName ? rowClassName(row) : null, onRowClick ? "row-clickable" : null]
                .filter(Boolean)
                .join(" ") || undefined;

            return (
              <tr
                key={rowKey(row)}
                className={className}
                // The row stands in for the link it replaced, so it stays
                // reachable and operable from the keyboard.
                tabIndex={onRowClick ? 0 : undefined}
                onClick={onRowClick ? (event) => handleRowClick(event, row) : undefined}
                onKeyDown={onRowClick ? (event) => handleRowKeyDown(event, row) : undefined}
              >
                {columns.map((column) => (
                  <td key={column.key} className={column.className}>
                    {column.render ? column.render(row) : row[column.key]}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

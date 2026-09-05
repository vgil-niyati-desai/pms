/**
 * Table driven by a column config, with the loading and empty states the
 * list screens all need.
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
  empty = null,
}) {
  if (loading) return <p className="muted">{loadingMessage}</p>;
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

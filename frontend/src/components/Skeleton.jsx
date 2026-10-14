/**
 * Placeholders for content that has not arrived yet.
 *
 * A table that collapses to one line of "Loading..." and then springs back to
 * full height moves everything under it twice, and says nothing in between
 * about what is coming. A placeholder at roughly the size of the real thing
 * lays the screen out before the data lands, so filling it in is the only
 * thing that changes.
 *
 * Only for a first load. A list that already has rows on screen keeps them
 * while it reloads -- the screens decide that, and they already do.
 */
export default function Skeleton({ width = "100%", height = 12, className }) {
  return (
    <span
      className={className ? `skeleton ${className}` : "skeleton"}
      aria-hidden="true"
      style={{ width, height }}
    />
  );
}

// Enough variety that a block of cells reads as text rather than as a grid of
// identical bars, without randomness that would redraw differently on every
// render. Walked by row + column, so neither lines nor columns repeat.
const CELL_WIDTHS = ["72%", "46%", "88%", "54%", "64%", "38%"];

/**
 * A table's shape while its rows are loading: the real header row, inside the
 * real scroll box, over placeholder cells.
 *
 * The headers are the genuine ones -- `columns` is the same config the table
 * itself is given -- so the column widths, the padding and the sort controls
 * are all where they will be once the rows arrive. They stay readable and
 * operable while the rows load, which also means they must not be hidden from
 * a screen reader: only the placeholder rows are, since the shape of absent
 * text is no use to one and there is nothing to focus inside them. `message`
 * is what it is told instead, once.
 */
export function TableSkeleton({ columns, rows = 5, message = "Loading..." }) {
  return (
    <>
      <p className="sr-only" role="status">
        {message}
      </p>
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
          <tbody aria-hidden="true">
            {Array.from({ length: rows }, (_, row) => (
              <tr key={row}>
                {columns.map((column, cell) => (
                  <td key={column.key} className={column.className}>
                    <Skeleton width={CELL_WIDTHS[(row + cell) % CELL_WIDTHS.length]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

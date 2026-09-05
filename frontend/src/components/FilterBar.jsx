/**
 * Search and filters above the list.
 *
 * The rail this replaced took a fixed column beside the table, which the wide
 * list tables could not spare. Here the search runs the full width of the
 * content area and the filters sit in one compact row beneath it, wrapping
 * onto another line rather than pushing the page sideways.
 *
 * `search` is the search control; `children` are the filters, in the order
 * they should read.
 */
export default function FilterBar({ search, children, onClear, showClear }) {
  return (
    <section className="filter-bar card">
      <div className="filter-search">{search}</div>
      <div className="filter-toolbar">
        {children}
        {showClear && (
          <button type="button" className="btn filter-clear" onClick={onClear}>
            Clear all
          </button>
        )}
      </div>
    </section>
  );
}

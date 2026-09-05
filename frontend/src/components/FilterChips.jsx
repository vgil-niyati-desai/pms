/**
 * The active filters, echoed above the results so what is narrowing the list
 * is visible without reading the rail. `chips` is
 * `[{ key, label, onRemove }]`.
 */
export default function FilterChips({ chips }) {
  if (!chips.length) return null;
  return (
    <div className="filter-chips">
      {chips.map((chip) => (
        <span key={chip.key} className="chip">
          {chip.label}
          <button type="button" className="chip-remove" onClick={chip.onRemove} aria-label={`Remove filter ${chip.label}`}>
            &times;
          </button>
        </span>
      ))}
    </div>
  );
}

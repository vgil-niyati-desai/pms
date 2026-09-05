/** A tag. `onRemove` turns it into a removable chip. */
export default function TagChip({ value, onRemove }) {
  return (
    <span className="tag tag-neutral">
      {value}
      {onRemove && (
        <button type="button" className="chip-remove" onClick={onRemove} aria-label={`Remove tag ${value}`}>
          &times;
        </button>
      )}
    </span>
  );
}

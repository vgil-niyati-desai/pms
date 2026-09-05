/**
 * Rounded label for a value from a small fixed set — document types today,
 * project and tender statuses later.
 *
 * The colour comes from a CSS class derived from the value ("Work Order" ->
 * `tag-work-order`), which is how the document-type pills were already
 * styled. A value with no matching class falls back to the neutral pill.
 */
export function pillSlug(value) {
  return String(value).trim().replace(/\s+/g, "-").toLowerCase();
}

export default function StatusPill({ value }) {
  if (value === null || value === undefined || value === "") return null;
  return <span className={`tag tag-${pillSlug(value)}`}>{value}</span>;
}

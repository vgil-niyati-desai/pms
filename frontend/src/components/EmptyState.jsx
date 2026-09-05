/**
 * Shown in place of results when a list has nothing to display.
 * `action` is an optional button or link, e.g. "Clear filters".
 */
export default function EmptyState({ message, action }) {
  return (
    <div className="empty-state">
      <p className="muted">{message}</p>
      {action}
    </div>
  );
}

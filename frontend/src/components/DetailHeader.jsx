/**
 * Identity block at the top of a record screen: what this is, the handful of
 * facts worth seeing before opening a tab, and the actions available on it.
 *
 * `facts` is `[{ label, value }]`; entries with no value are dropped rather
 * than shown empty.
 */
export default function DetailHeader({ title, subtitle, facts = [], badges = null, actions = null }) {
  const shown = facts.filter((fact) => fact.value !== null && fact.value !== undefined && fact.value !== "");

  return (
    <header className="detail-header card">
      <div className="detail-header-top">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="muted detail-subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="detail-actions">{actions}</div>}
      </div>

      {shown.length > 0 && (
        <dl className="detail-facts">
          {shown.map((fact) => (
            <div key={fact.label}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {badges && <div className="detail-badges">{badges}</div>}
    </header>
  );
}

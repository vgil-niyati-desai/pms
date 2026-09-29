/**
 * Identity block at the top of a record screen: what this is, the handful of
 * facts worth seeing before opening a tab, and the actions available on it.
 *
 * `facts` is `[{ label, value }]`; entries with no value are dropped rather
 * than shown empty.
 *
 * `titleBadges` sit on the title's own line -- a record's state belongs
 * beside its name, not below the facts where it reads as one more of them.
 * `badges` stays the row underneath, for the many small things (tags).
 * `subtitleLabel` names what the subtitle is, so a long organisation name
 * is not left to be guessed at from its position.
 *
 * Both are optional: a screen passing neither renders exactly as before.
 */
export default function DetailHeader({
  title,
  subtitle,
  subtitleLabel = null,
  facts = [],
  titleBadges = null,
  badges = null,
  actions = null,
}) {
  const shown = facts.filter((fact) => fact.value !== null && fact.value !== undefined && fact.value !== "");

  return (
    <header className="detail-header card">
      <div className="detail-header-top">
        <div>
          <div className="detail-title-row">
            <h2>{title}</h2>
            {titleBadges}
          </div>
          {subtitle && (
            <p className="muted detail-subtitle">
              {subtitleLabel && (
                <span className="detail-subtitle-label">{subtitleLabel}</span>
              )}
              {subtitle}
            </p>
          )}
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

import { EVIDENCE_TYPES } from "./projectFields";

/**
 * Which of the evidence documents a project holds. This is the answer
 * the list and the overview both exist to give: whether the project can be
 * cited in a tender yet.
 */
export default function EvidenceStrip({
  heldTypes,
  compact = false,
  onMissingClick,
  onHeldClick,
}) {
  return (
    <div className={compact ? "evidence-strip evidence-compact" : "evidence-strip"}>
      {EVIDENCE_TYPES.map((type) => {
        const held = heldTypes.includes(type);
        const label = compact ? type.replace("Completion Certificate", "Completion Cert.") : type;
        const className = held ? "evidence-item evidence-yes" : "evidence-item evidence-no";
        // The mark leads the pill and is the same width either way, so the
        // whole strip reads as one column of states rather than as labels of
        // differing length with something on the end of each.
        const mark = (
          <span className="evidence-mark" aria-hidden="true">
            {held ? "✓" : "—"}
          </span>
        );

        // A held pill answers "which document proves this?" by taking you
        // to that type's group on the Documents tab. Without it half the
        // strip was clickable and half was not, with nothing to say which.
        if (held && onHeldClick) {
          return (
            <button
              key={type}
              type="button"
              className={`${className} evidence-action`}
              onClick={() => onHeldClick(type)}
            >
              {mark}
              <span className="evidence-label">{label}</span>
              <span className="sr-only">present — show the documents</span>
            </button>
          );
        }

        if (!held && onMissingClick) {
          return (
            <button
              key={type}
              type="button"
              className={`${className} evidence-action`}
              onClick={() => onMissingClick(type)}
            >
              {mark}
              <span className="evidence-label">{label}</span>
              {/* The state and the action are two different things: the dash
                  says it is missing, this says what clicking will do. Spelt
                  out rather than left as a "+", which only reads as an
                  action once you already know it is one. */}
              <span className="evidence-add" aria-hidden="true">
                + Add
              </span>
              <span className="sr-only">missing — add one</span>
            </button>
          );
        }
        return (
          <span key={type} className={className}>
            {mark}
            <span className="evidence-label">{label}</span>
            <span className="sr-only">{held ? "present" : "missing"}</span>
          </span>
        );
      })}
    </div>
  );
}

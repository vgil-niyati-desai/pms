import { EVIDENCE_TYPES } from "./projectFields";

/**
 * Which of the evidence documents a project holds. This is the answer
 * the list and the overview both exist to give: whether the project can be
 * cited in a tender yet.
 */
export default function EvidenceStrip({ heldTypes, compact = false, onMissingClick }) {
  return (
    <div className={compact ? "evidence-strip evidence-compact" : "evidence-strip"}>
      {EVIDENCE_TYPES.map((type) => {
        const held = heldTypes.includes(type);
        const label = compact ? type.replace("Completion Certificate", "Completion Cert.") : type;
        const className = held ? "evidence-item evidence-yes" : "evidence-item evidence-no";

        if (!held && onMissingClick) {
          return (
            <button key={type} type="button" className={`${className} evidence-action`} onClick={() => onMissingClick(type)}>
              {label} <span aria-hidden="true">+</span>
              <span className="sr-only">missing — add one</span>
            </button>
          );
        }
        return (
          <span key={type} className={className}>
            {label} <span aria-hidden="true">{held ? "✓" : "—"}</span>
            <span className="sr-only">{held ? "present" : "missing"}</span>
          </span>
        );
      })}
    </div>
  );
}

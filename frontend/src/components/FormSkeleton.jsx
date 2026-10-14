import Skeleton from "./Skeleton";

// Every text-like control in these forms -- input, select, the tag box --
// renders at this height; only a textarea is taller. Matching both is what
// keeps the page from jumping once the real form replaces this.
const CONTROL_HEIGHT = 40;
const TEXTAREA_HEIGHT = 84;

/**
 * A create/edit form's shape while the record it edits is loading: the same
 * card, the same title, the same rows of two fields side by side, so the
 * one thing that changes when the record arrives is the text.
 *
 * `rows` is how many two-field rows come first -- every field these forms
 * share is one of a pair, down to the last row before the full-width ones.
 * `wide` is the full-width fields that follow, one entry per field, each
 * either `"control"` (a select, a tag box) or `"textarea"` (notes, a
 * summary) -- whichever that page actually has, in order. `actions` is how
 * many buttons sit in the footer: three once a record can be deleted, two
 * on a form that cannot yet.
 */
export default function FormSkeleton({ rows = 4, wide = ["textarea"], actions = 3 }) {
  return (
    <>
      <p className="sr-only" role="status">
        Loading...
      </p>
      <div className="card form form-skeleton" aria-hidden="true">
        <Skeleton width="30%" height={22} />

        {Array.from({ length: rows }, (_, row) => (
          <div className="form-row" key={`row-${row}`}>
            {[0, 1].map((col) => (
              <div className="field" key={col}>
                <Skeleton width="42%" height={9} />
                <Skeleton height={CONTROL_HEIGHT} />
              </div>
            ))}
          </div>
        ))}

        {wide.map((kind, index) => (
          <div className="field" key={`wide-${index}`}>
            <Skeleton width="20%" height={9} />
            <Skeleton height={kind === "textarea" ? TEXTAREA_HEIGHT : CONTROL_HEIGHT} />
          </div>
        ))}

        <div className="form-actions">
          {Array.from({ length: actions }, (_, index) => (
            <Skeleton
              key={index}
              width={index === 0 ? 132 : 100}
              height={34}
              // The third button is Delete, pushed to the far edge like the
              // real one.
              className={index === 2 ? "form-actions-end" : undefined}
            />
          ))}
        </div>
      </div>
    </>
  );
}

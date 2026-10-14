import { Children, cloneElement, isValidElement } from "react";

/**
 * Label + control + optional hint, the wrapper every form input in the app
 * already used. Pass the control as `children`; `htmlFor` should match its id.
 *
 * `required` and `error` describe that control: the child whose id matches
 * `htmlFor` is given aria-required, aria-invalid and an aria-describedby
 * pointing at the hint and the error message, so each caller only says what
 * is wrong rather than wiring the attributes up by hand.
 */
export default function Field({
  label,
  htmlFor,
  hint,
  error,
  required = false,
  className = "field",
  children,
}) {
  const hintId = hint && htmlFor ? `${htmlFor}-hint` : undefined;
  const errorId = error && htmlFor ? `${htmlFor}-error` : undefined;
  const control = htmlFor
    ? Children.map(children, (child) => {
        if (!isValidElement(child) || child.props.id !== htmlFor) return child;
        // Only adds what applies, so nothing the caller set is overwritten.
        const extra = {};
        if (required) extra["aria-required"] = true;
        if (error) extra["aria-invalid"] = true;
        const describedBy = [child.props["aria-describedby"], errorId, hintId]
          .filter(Boolean)
          .join(" ");
        if (describedBy) extra["aria-describedby"] = describedBy;
        return cloneElement(child, extra);
      })
    : children;

  return (
    <div className={error ? `${className} field-invalid` : className}>
      {label && <label htmlFor={htmlFor}>{label}</label>}
      {control}
      {error && (
        <p id={errorId} className="field-error">
          {error}
        </p>
      )}
      {hint && (
        <p id={hintId} className="muted hint">
          {hint}
        </p>
      )}
    </div>
  );
}

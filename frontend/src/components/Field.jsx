/**
 * Label + control + optional hint, the wrapper every form input in the app
 * already used. Pass the control as `children`; `htmlFor` should match its id.
 */
export default function Field({ label, htmlFor, hint, className = "field", children }) {
  return (
    <div className={className}>
      {label && <label htmlFor={htmlFor}>{label}</label>}
      {children}
      {hint && <p className="muted hint">{hint}</p>}
    </div>
  );
}

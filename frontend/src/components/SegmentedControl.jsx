/**
 * Two or three mutually exclusive views of the same screen, e.g. employees
 * against certifications. `options` is `[{ key, label }]`.
 */
export default function SegmentedControl({ options, value, onChange, label }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={option.key === value}
          className={option.key === value ? "segment segment-active" : "segment"}
          onClick={() => onChange(option.key)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

import { useId } from "react";

/** Multi-select as a list of checkboxes, for short fixed option sets. */
export default function CheckboxGroup({ legend, options, value, onChange }) {
  const name = useId();

  function toggle(option) {
    onChange(value.includes(option) ? value.filter((v) => v !== option) : [...value, option]);
  }

  return (
    <fieldset className="checkbox-group">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option} className="checkbox-row" htmlFor={`${name}-${option}`}>
          <input
            id={`${name}-${option}`}
            type="checkbox"
            checked={value.includes(option)}
            onChange={() => toggle(option)}
          />
          {option}
        </label>
      ))}
    </fieldset>
  );
}

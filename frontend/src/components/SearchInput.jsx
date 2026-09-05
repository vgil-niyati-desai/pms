import { useEffect, useState } from "react";
import Field from "./Field";
import useDebounce from "../hooks/useDebounce";

/**
 * Search box that reports its value once typing pauses, so every keystroke
 * doesn't trigger a query. `value` is the committed value; typing locally is
 * immediate and only `onChange` is delayed.
 */
export default function SearchInput({ id, label = "Search", value, onChange, placeholder }) {
  const [text, setText] = useState(value);
  const debounced = useDebounce(text, 300);

  // Reflect a value cleared or changed from outside (e.g. "Clear all").
  useEffect(() => {
    setText(value);
  }, [value]);

  useEffect(() => {
    if (debounced !== value) onChange(debounced);
    // onChange identity is not stable across renders; the debounced text is
    // the only thing that should drive this.
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Field label={label} htmlFor={id}>
      <input
        id={id}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
      />
    </Field>
  );
}

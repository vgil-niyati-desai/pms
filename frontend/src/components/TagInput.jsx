import { useId, useState } from "react";
import Field from "./Field";
import TagChip from "./TagChip";

/**
 * Free-text tag entry with suggestions from the tags already in use.
 * Enter or comma commits a tag; Backspace on an empty box removes the last.
 *
 * `id` names the text box, for a form that needs to find it again -- to move
 * focus to it when `error` flags it, say. Without one an id is generated.
 */
export default function TagInput({
  id,
  label = "Tags",
  value,
  onChange,
  suggestions = [],
  hint,
  error,
}) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const listId = `${inputId}-suggestions`;
  const [text, setText] = useState("");

  function addTag(raw) {
    const tag = raw.trim();
    if (!tag) return;
    // Case-insensitive de-dupe, keeping whatever spelling is already stored.
    if (!value.some((existing) => existing.toLowerCase() === tag.toLowerCase())) {
      onChange([...value, tag]);
    }
    setText("");
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" || e.key === ",") {
      // Enter would otherwise submit the surrounding form.
      e.preventDefault();
      addTag(text);
    } else if (e.key === "Backspace" && !text && value.length) {
      onChange(value.slice(0, -1));
    }
  }

  const unused = suggestions.filter(
    (tag) => !value.some((existing) => existing.toLowerCase() === tag.toLowerCase())
  );

  return (
    <Field label={label} htmlFor={inputId} hint={hint} error={error}>
      <div className="tag-input">
        {value.map((tag) => (
          <TagChip key={tag} value={tag} onRemove={() => onChange(value.filter((t) => t !== tag))} />
        ))}
        <input
          id={inputId}
          type="text"
          list={listId}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => addTag(text)}
          // Nested inside the chip box, so Field cannot reach it to add these.
          aria-invalid={error ? true : undefined}
          aria-describedby={
            [error && `${inputId}-error`, hint && `${inputId}-hint`].filter(Boolean).join(" ") ||
            undefined
          }
          placeholder={value.length ? "" : "Type a tag and press Enter"}
        />
        <datalist id={listId}>
          {unused.map((tag) => (
            <option key={tag} value={tag} />
          ))}
        </datalist>
      </div>
    </Field>
  );
}

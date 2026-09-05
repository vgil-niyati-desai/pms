import { useId, useState } from "react";
import Field from "./Field";
import TagChip from "./TagChip";

/**
 * Free-text tag entry with suggestions from the tags already in use.
 * Enter or comma commits a tag; Backspace on an empty box removes the last.
 */
export default function TagInput({ label = "Tags", value, onChange, suggestions = [], hint }) {
  const inputId = useId();
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
    <Field label={label} htmlFor={inputId} hint={hint}>
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

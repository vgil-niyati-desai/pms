import Field from "./Field";

/** File types the backend accepts (see storage.allowed_file). */
export const ACCEPTED_FILE_TYPES = ".pdf,.png,.jpg,.jpeg";

/**
 * File picker for document uploads.
 *
 * The caller keeps a ref to the underlying input so it can clear the picker
 * after a save — React 19 passes `ref` straight through as a prop.
 */
export default function FileUpload({ id, name, label, hint, accept = ACCEPTED_FILE_TYPES, onChange, ref }) {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <input id={id} name={name} type="file" accept={accept} onChange={onChange} ref={ref} />
    </Field>
  );
}

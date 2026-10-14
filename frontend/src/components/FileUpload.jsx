import Field from "./Field";
import { formatFileSize } from "../lib/format";

/** File types the backend accepts (see storage.allowed_file). */
export const ACCEPTED_FILE_TYPES = ".pdf,.png,.jpg,.jpeg";

/** The largest upload the backend accepts. Must match MAX_UPLOAD_BYTES in
 *  backend/app/storage.py, which enforces it again. */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const MAX_UPLOAD_LABEL = "20 MB";

const ACCEPTED_EXTENSIONS = ACCEPTED_FILE_TYPES.split(",").map((ext) =>
  ext.replace(".", "").toLowerCase(),
);

/**
 * Whether a picked file's extension is one the backend will accept.
 *
 * The backend's own check (storage.allowed_file) is what actually decides —
 * this only catches an obviously wrong file before a round trip, the way the
 * `accept` attribute would if every browser enforced it on a drag-drop or an
 * "all files" picker. No file at all is not a type error; that is what
 * leaving the field empty means.
 */
export function isAcceptedFileType(file) {
  if (!file) return true;
  const match = /\.([a-z0-9]+)$/i.exec(file.name || "");
  return Boolean(match) && ACCEPTED_EXTENSIONS.includes(match[1].toLowerCase());
}

/**
 * Why a picked file cannot be uploaded, or null when it can (or when nothing
 * was picked). The same three rules the backend applies -- type, empty, size
 * -- caught here so the person hears about it on picking the file rather
 * than after a round trip.
 */
export function fileProblem(file) {
  if (!file) return null;
  if (!isAcceptedFileType(file)) {
    return "That file type isn't supported. Please choose a PDF, PNG, or JPG.";
  }
  if (file.size === 0) return `"${file.name}" is empty (0 bytes). Please choose the actual file.`;
  if (file.size > MAX_UPLOAD_BYTES) {
    return `"${file.name}" is ${formatFileSize(file.size)}, over the ${MAX_UPLOAD_LABEL} limit. Please choose a smaller or compressed copy.`;
  }
  return null;
}

/**
 * A picker's change handler for a form using useFieldErrors: keeps the file,
 * or -- when it cannot be uploaded -- shows why on `fieldId` and empties the
 * picker, so what it shows matches what would be sent.
 */
export function takePickedFile(e, { fieldId, fieldErrors, setFile }) {
  const picked = e.target.files[0] || null;
  const problem = fileProblem(picked);
  if (problem) {
    fieldErrors.set(fieldId, problem);
    e.target.value = "";
    setFile(null);
    return;
  }
  fieldErrors.clear(fieldId);
  setFile(picked);
}

/**
 * File picker for document uploads.
 *
 * The caller keeps a ref to the underlying input so it can clear the picker
 * after a save — React 19 passes `ref` straight through as a prop.
 */
export default function FileUpload({
  id,
  name,
  label,
  hint,
  error,
  required = false,
  accept = ACCEPTED_FILE_TYPES,
  onChange,
  ref,
}) {
  return (
    <Field label={label} htmlFor={id} hint={hint} error={error} required={required}>
      <input id={id} name={name} type="file" accept={accept} onChange={onChange} ref={ref} />
    </Field>
  );
}

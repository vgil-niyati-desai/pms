/**
 * Save a Blob to disk under `fileName`, from the bytes already in hand.
 *
 * A plain link to the server navigates the page to the error body when the
 * file cannot be served -- the whole app replaced by a line of JSON. A Blob
 * has already been served, so saving it cannot fail that way. The object
 * URL exists only for the click and is revoked straight after.
 */
export function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName || "document";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

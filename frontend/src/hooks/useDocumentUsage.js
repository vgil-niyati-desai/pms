import { useCallback } from "react";
import useResource from "./useResource";
import { getDocumentUsage } from "../api/documents";

/**
 * What else uses a document, apart from the record asking.
 *
 * `owner` names that record the way the delete will (see ownerQuery in
 * api/documents.js), so `otherUses` is exactly what a delete from here would
 * be refused over: empty means this record is the document's only user and
 * deleting it is safe.
 *
 * `pending` is true until the answer is in, and a screen should offer
 * neither Delete nor "keep the file" before then -- guessing either way is
 * the risk the check exists to remove. If the check fails, `otherUses` stays
 * empty and Delete is offered: the server makes the same check again on the
 * delete itself and refuses with a 409, after which `reload` asks again.
 *
 * With no `documentId` (a new record, or one without a file) nothing is
 * fetched and nothing is pending.
 */
export default function useDocumentUsage(documentId, owner = {}) {
  const { fromProject, fromTender, fromEmployee, fromItem } = owner;
  const load = useCallback(
    () =>
      documentId
        ? getDocumentUsage(documentId, { fromProject, fromTender, fromEmployee, fromItem })
        : Promise.resolve([]),
    [documentId, fromProject, fromTender, fromEmployee, fromItem],
  );
  const usage = useResource(load, { initialData: [] });
  return {
    otherUses: usage.data ?? [],
    pending: Boolean(documentId) && usage.loading,
    reload: usage.reload,
  };
}

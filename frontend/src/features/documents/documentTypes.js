import { EVIDENCE_TYPES } from "../projects/projectFields";
import { TENDER_DOCUMENT_TYPES } from "../tenders/tenderFields";
import {
  CERTIFICATION_DOCUMENT_TYPE,
  CV_DOCUMENT_TYPE,
  TENDER_CERTIFICATE_DOCUMENT_TYPE,
  TENDER_RECEIPT_DOCUMENT_TYPE,
} from "../../api/attachments";

/**
 * Every type the app itself writes to the document log: project evidence,
 * tender papers, and the files behind CVs, certifications, receipts and
 * tender certificates. The log is shared by all of them, so its filter and
 * its edit form have to know every one -- not only the five the Documents
 * form used to offer, which left a CV or a receipt with a blank type.
 */
export const KNOWN_DOCUMENT_TYPES = [
  ...EVIDENCE_TYPES,
  ...TENDER_DOCUMENT_TYPES,
  CV_DOCUMENT_TYPE,
  CERTIFICATION_DOCUMENT_TYPE,
  TENDER_RECEIPT_DOCUMENT_TYPE,
  TENDER_CERTIFICATE_DOCUMENT_TYPE,
];

/**
 * The options for a type picker: the known types, then any other type
 * already in the log (imported entries carry their own), then `current` if it
 * is still missing -- so an existing document always shows its own type.
 */
export function documentTypeOptions(storedTypes = [], current = "") {
  const options = [...KNOWN_DOCUMENT_TYPES];
  for (const type of [...storedTypes, current]) {
    if (type && !options.includes(type)) options.push(type);
  }
  return options;
}

/**
 * The options for a category picker: every category the log already holds,
 * plus `current` if it is not among them.
 *
 * No known list to lead with, unlike the types above -- a category is free
 * text, so the vocabulary is only ever what has been written. The `current`
 * rule is the same one and matters more here: a filter carrying an older
 * spelling, or one arriving in a link, has to appear as the filter that is
 * applied. Dropping it would leave the box reading as unset while the list
 * below stayed narrowed by it.
 */
export function documentCategoryOptions(storedCategories = [], current = "") {
  const options = [...storedCategories];
  if (current && !options.includes(current)) options.push(current);
  return options;
}

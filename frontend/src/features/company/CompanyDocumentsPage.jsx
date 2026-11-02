import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  STATUS_LABELS,
  createCompanyDocument,
  getCompanyDocumentFacets,
  listCompanyDocuments,
  updateCompanyDocument,
} from "../../api/companyDocuments";
import { deleteDocument } from "../../api/documents";
import ConfirmDelete from "../../components/ConfirmDelete";
import DataTable from "../../components/DataTable";
import Drawer, { useRequestDrawerClose } from "../../components/Drawer";
import DocumentFileSection from "../../components/DocumentFileSection";
import EmptyState from "../../components/EmptyState";
import Field from "../../components/Field";
import FileUpload, { fileProblem } from "../../components/FileUpload";
import FileViewLink from "../../components/FileViewLink";
import FilterBar from "../../components/FilterBar";
import FilterChips from "../../components/FilterChips";
import FormRow from "../../components/FormRow";
import Pagination from "../../components/Pagination";
import SearchInput from "../../components/SearchInput";
import StatusPill from "../../components/StatusPill";
import useHandoffNotice from "../../hooks/useHandoffNotice";
import usePageRecovery, { wholePage } from "../../hooks/usePageRecovery";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { useAuth } from "../../app/AuthContext";
import { formatFileSize } from "../../lib/format";

const PAGE_SIZE = 15;
const dash = (value) => value || "—";
const FILTER_SCHEMA = {
  q: "",
  category: "",
  subcategory: "",
  type: "",
  organisation: "",
  status: "",
  sort: "-created_at",
  page: 1,
};

const EMPTY_FORM = {
  title: "",
  document_type: "",
  category: "",
  subcategory: "",
  issuing_organisation: "",
  certificate_number: "",
  issue_date: "",
  expiry_date: "",
  notes: "",
};

const formFrom = (doc) =>
  Object.fromEntries(Object.keys(EMPTY_FORM).map((key) => [key, doc?.[key] ?? ""]));

// Shorter than the filter's labels so the Status column stays narrow; the
// filter and the details drawer carry the full wording.
const SHORT_STATUS = { unknown: "Not recorded" };

function statusPill(row) {
  const label = SHORT_STATUS[row.status] || STATUS_LABELS[row.status] || row.status;
  return <StatusPill value={label} />;
}

/** Suggestions only: every field still accepts whatever is typed. */
function Suggestions({ id, facet }) {
  return (
    <datalist id={id}>
      {(facet || []).map((f) => (
        <option key={f.value} value={f.value} />
      ))}
    </datalist>
  );
}

function CompanyDocumentForm({ document, facets, canReplace, onSaved, onDirtyChange }) {
  const editing = Boolean(document);
  const initial = useMemo(() => formFrom(document), [document]);
  const [form, setForm] = useState(initial);
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);
  const cancel = useRequestDrawerClose();

  const dirty = file !== null || JSON.stringify(form) !== JSON.stringify(initial);
  useEffect(() => {
    if (onDirtyChange) onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  const change = (e) => setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));

  function pickFile(e) {
    const picked = e.target.files[0] || null;
    const problem = fileProblem(picked);
    setFileError(problem);
    if (problem) {
      e.target.value = "";
      setFile(null);
    } else {
      setFile(picked);
    }
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (!form.title.trim()) return setError("Please fill in the title.");
    if (!editing && !file) return setFileError("Please choose the certificate file.");
    if (form.issue_date && form.expiry_date && form.expiry_date < form.issue_date) {
      return setError("The expiry date cannot be before the issue date.");
    }
    setBusy(true);
    try {
      const saved = editing
        ? await updateCompanyDocument(document.id, form, file)
        : await createCompanyDocument(form, file);
      onSaved(saved, editing);
    } catch (err) {
      setError(err.message || "Something went wrong while saving.");
      setBusy(false);
    }
  }

  const fileHint = file
    ? `Selected: ${file.name} (${formatFileSize(file.size)})`
    : editing
      ? `Current file: ${document.file_name || "none"}. Leave empty to keep it.`
      : "PDF, PNG or JPG, up to 20 MB.";

  return (
    <form onSubmit={submit}>
      {error && <div className="alert alert-error">{error}</div>}

      <Field label="Title *" htmlFor="title" required>
        <input id="title" name="title" type="text" value={form.title} onChange={change} />
      </Field>
      <FormRow>
        <Field label="Category" htmlFor="category" hint="Pick one in use or type a new one.">
          <input id="category" name="category" type="text" list="cd-categories" value={form.category} onChange={change} />
          <Suggestions id="cd-categories" facet={facets?.categories} />
        </Field>
        <Field label="Subcategory" htmlFor="subcategory">
          <input id="subcategory" name="subcategory" type="text" list="cd-subcategories" value={form.subcategory} onChange={change} />
          <Suggestions id="cd-subcategories" facet={facets?.subcategories} />
        </Field>
      </FormRow>
      <FormRow>
        <Field label="Document type" htmlFor="document_type">
          <input id="document_type" name="document_type" type="text" list="cd-types" value={form.document_type} onChange={change} />
          <Suggestions id="cd-types" facet={facets?.document_types} />
        </Field>
        <Field label="Issuing organisation" htmlFor="issuing_organisation">
          <input id="issuing_organisation" name="issuing_organisation" type="text" list="cd-orgs" value={form.issuing_organisation} onChange={change} />
          <Suggestions id="cd-orgs" facet={facets?.organisations} />
        </Field>
      </FormRow>
      <FormRow>
        <Field label="Certificate / registration number" htmlFor="certificate_number">
          <input id="certificate_number" name="certificate_number" type="text" value={form.certificate_number} onChange={change} />
        </Field>
        <span />
      </FormRow>
      <FormRow>
        <Field label="Issue date" htmlFor="issue_date">
          <input id="issue_date" name="issue_date" type="date" value={form.issue_date} onChange={change} />
        </Field>
        <Field label="Expiry date" htmlFor="expiry_date" hint="Leave blank if the document shows none.">
          <input id="expiry_date" name="expiry_date" type="date" value={form.expiry_date} onChange={change} />
        </Field>
      </FormRow>
      <Field label="Notes" htmlFor="notes">
        <textarea id="notes" name="notes" rows={3} value={form.notes} onChange={change} />
      </Field>

      {editing && !canReplace ? (
        <p className="muted">{fileHint} HODs can edit details but cannot replace the file.</p>
      ) : (
        <FileUpload
          id="document_file"
          name="document_file"
          label={editing ? "Replace file (optional)" : "Certificate file *"}
          hint={fileHint}
          error={fileError}
          onChange={pickFile}
          ref={fileRef}
        />
      )}

      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Saving..." : editing ? "Save changes" : "Add document"}
        </button>
        <button type="button" className="btn" onClick={cancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function CompanyDocumentsPage() {
  const { user } = useAuth();
  const { values, setValues } = useQueryParams(FILTER_SCHEMA);
  const [refreshKey, setRefreshKey] = useState(0);
  const [drawer, setDrawer] = useState(null); // null | { document } (document null = new)
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useHandoffNotice();
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const reportDirty = useCallback((value) => setDirty(value), []);

  const page = wholePage(values.page);
  const load = useCallback(
    () =>
      listCompanyDocuments({
        q: values.q,
        category: values.category,
        subcategory: values.subcategory,
        documentType: values.type,
        organisation: values.organisation,
        status: values.status,
        sort: values.sort,
        page,
        pageSize: PAGE_SIZE,
      }),
    [values, page, refreshKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const documents = useResource(load, { initialData: { items: [], total: 0 } });
  const movingPage = usePageRecovery({
    rawPage: values.page,
    page,
    resource: documents,
    pageSize: PAGE_SIZE,
    setPage: (p) => setValues({ page: p }),
  });
  const loadFacets = useCallback(() => getCompanyDocumentFacets(), [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const facets = useResource(loadFacets, { initialData: null });
  const rows = documents.data.items;
  const total = documents.data.total;

  const setFilter = (patch) => setValues({ ...patch, page: 1 });
  const clearFilters = () =>
    setFilter({ q: "", category: "", subcategory: "", type: "", organisation: "", status: "" });
  const toggleSort = (key) => setFilter({ sort: values.sort === key ? `-${key}` : key });

  function sortHeader(key, label) {
    const desc = values.sort === `-${key}`;
    const active = desc || values.sort === key;
    return (
      <button type="button" className={active ? "sort-header sort-active" : "sort-header"} onClick={() => toggleSort(key)}>
        {label}
        <span aria-hidden="true">{active ? (desc ? " ▼" : " ▲") : ""}</span>
      </button>
    );
  }

  function closeDrawer() {
    setDrawer(null);
    setDirty(false);
  }

  function saved(record, wasEditing) {
    setRefreshKey((k) => k + 1);
    closeDrawer();
    setNotice(wasEditing ? "Company document saved." : "Company document added.");
  }

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteDocument(pendingDelete.id);
      setPendingDelete(null);
      setNotice("Company document deleted.");
      if (rows.length === 1 && page > 1) setValues({ page: page - 1 });
      else documents.reload();
      facets.reload();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this document.");
    } finally {
      setDeleting(false);
    }
  }

  const options = (facet) => facet || [];
  const chips = [
    values.q && { key: "q", label: `Search: ${values.q}`, onRemove: () => setFilter({ q: "" }) },
    values.category && { key: "category", label: `Category: ${values.category}`, onRemove: () => setFilter({ category: "" }) },
    values.subcategory && { key: "subcategory", label: `Subcategory: ${values.subcategory}`, onRemove: () => setFilter({ subcategory: "" }) },
    values.type && { key: "type", label: `Type: ${values.type}`, onRemove: () => setFilter({ type: "" }) },
    values.organisation && { key: "organisation", label: `Issuer: ${values.organisation}`, onRemove: () => setFilter({ organisation: "" }) },
    values.status && { key: "status", label: `Status: ${STATUS_LABELS[values.status] || values.status}`, onRemove: () => setFilter({ status: "" }) },
  ].filter(Boolean);
  const filtering = chips.length > 0;

  const columns = [
    {
      key: "title",
      header: sortHeader("title", "Document"),
      className: "cell-name",
      render: (r) => (
        <>
          {r.title}
          {(r.certificate_number || r.document_type) && (
            <div className="muted">
              {[r.document_type, r.certificate_number && `No. ${r.certificate_number}`].filter(Boolean).join(" · ")}
            </div>
          )}
        </>
      ),
    },
    { key: "category", header: sortHeader("category", "Category"), render: (r) => dash([r.category, r.subcategory].filter(Boolean).join(" › ")) },
    { key: "issuing_organisation", header: "Issuer", render: (r) => dash(r.issuing_organisation) },
    { key: "issue_date", header: sortHeader("issue_date", "Issued"), className: "cell-tight", render: (r) => dash(r.issue_date) },
    { key: "expiry_date", header: sortHeader("expiry_date", "Expires"), className: "cell-tight", render: (r) => dash(r.expiry_date) },
    { key: "status", header: "Status", className: "cell-tight", render: statusPill },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      render: (r) => (
        <FileViewLink
          documentId={r.has_file ? r.id : null}
          fileName={r.file_name}
          title={r.title}
          onChanged={documents.reload}
        />
      ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (r) => (
        <div className="row-actions">
          <button type="button" className="btn btn-sm btn-danger" onClick={() => { setDeleteError(null); setPendingDelete(r); }}>
            Delete
          </button>
        </div>
      ),
    },
  ];

  return (
    <>
      {notice && (
        <div className="alert alert-success notice-bar" role="status">
          <span>{notice}</span>
          <button type="button" className="notice-dismiss" onClick={() => setNotice(null)} aria-label="Dismiss this message">
            &times;
          </button>
        </div>
      )}

      <div className="list-layout">
        <FilterBar
          showClear={filtering}
          onClear={clearFilters}
          search={
            <SearchInput
              id="company-document-search"
              value={values.q}
              onChange={(q) => setFilter({ q })}
              placeholder="Title, issuer, certificate number, file name, notes"
            />
          }
        >
          <Field label="Category" htmlFor="cd-filter-category">
            <select id="cd-filter-category" value={values.category} onChange={(e) => setFilter({ category: e.target.value })}>
              <option value="">All categories</option>
              {options(facets.data?.categories).map((f) => (
                <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
              ))}
              {values.category && !options(facets.data?.categories).some((f) => f.value === values.category) && (
                <option value={values.category}>{values.category}</option>
              )}
            </select>
          </Field>
          <Field label="Subcategory" htmlFor="cd-filter-subcategory">
            <select id="cd-filter-subcategory" value={values.subcategory} onChange={(e) => setFilter({ subcategory: e.target.value })}>
              <option value="">All subcategories</option>
              {options(facets.data?.subcategories).map((f) => (
                <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
              ))}
              {values.subcategory && !options(facets.data?.subcategories).some((f) => f.value === values.subcategory) && (
                <option value={values.subcategory}>{values.subcategory}</option>
              )}
            </select>
          </Field>
          <Field label="Type" htmlFor="cd-filter-type">
            <select id="cd-filter-type" value={values.type} onChange={(e) => setFilter({ type: e.target.value })}>
              <option value="">All types</option>
              {options(facets.data?.document_types).map((f) => (
                <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
              ))}
              {values.type && !options(facets.data?.document_types).some((f) => f.value === values.type) && (
                <option value={values.type}>{values.type}</option>
              )}
            </select>
          </Field>
          <Field label="Issuer" htmlFor="cd-filter-org">
            <select id="cd-filter-org" value={values.organisation} onChange={(e) => setFilter({ organisation: e.target.value })}>
              <option value="">All issuers</option>
              {options(facets.data?.organisations).map((f) => (
                <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
              ))}
              {values.organisation && !options(facets.data?.organisations).some((f) => f.value === values.organisation) && (
                <option value={values.organisation}>{values.organisation}</option>
              )}
            </select>
          </Field>
          <Field label="Status" htmlFor="cd-filter-status">
            <select id="cd-filter-status" value={values.status} onChange={(e) => setFilter({ status: e.target.value })}>
              <option value="">Any status</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => {
                const count = facets.data?.statuses?.find((s) => s.value === value)?.count;
                return (
                  <option key={value} value={value}>{label}{count !== undefined ? ` (${count})` : ""}</option>
                );
              })}
            </select>
          </Field>
        </FilterBar>

        <section className="card list-panel">
          <div className="list-head">
            <h2>Company documents {!documents.loading && !documents.error && `(${total})`}</h2>
            <button type="button" className="btn btn-primary" onClick={() => { setDirty(false); setDrawer({ document: null }); }}>
              + Add company document
            </button>
          </div>
          <FilterChips chips={chips} />
          <DataTable
            columns={columns}
            rows={rows}
            loading={documents.loading || movingPage}
            error={documents.error}
            onRetry={documents.reload}
            rowClassName={(r) => (drawer?.document?.id === r.id ? "row-editing" : undefined)}
            onRowClick={(row) => { setDirty(false); setDrawer({ document: row }); }}
            empty={
              filtering ? (
                <EmptyState
                  message="No company documents match your filters."
                  action={<button type="button" className="btn btn-sm" onClick={clearFilters}>Clear all filters</button>}
                />
              ) : (
                <EmptyState message="No company documents yet." />
              )
            }
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={(p) => setValues({ page: p })} />
        </section>
      </div>

      {drawer && (
        <Drawer title={drawer.document ? drawer.document.title : "Add company document"} onClose={closeDrawer} dirty={dirty}>
          {drawer.document && (
            <DocumentFileSection
              documentId={drawer.document.has_file ? drawer.document.id : null}
              fileName={drawer.document.file_name}
              title={drawer.document.title}
            />
          )}
          <CompanyDocumentForm
            key={drawer.document?.id || "new"}
            document={drawer.document}
            facets={facets.data}
            canReplace={user.role === "Admin"}
            onSaved={saved}
            onDirtyChange={reportDirty}
          />
        </Drawer>
      )}

      {pendingDelete && (
        <ConfirmDelete
          title="Delete this company document?"
          busy={deleting}
          error={deleteError}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        >
          <p className="muted">
            {pendingDelete.title}
            {pendingDelete.certificate_number ? ` · ${pendingDelete.certificate_number}` : ""}
          </p>
          <p className="muted">
            The record and its stored file ({pendingDelete.file_name || "no file"}) will be permanently
            removed. This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

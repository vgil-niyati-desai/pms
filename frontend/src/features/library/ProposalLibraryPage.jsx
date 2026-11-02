import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createProposalItem,
  deleteProposalItem,
  getProposalItem,
  listProposalItems,
  listProposalSections,
  proposalFileUrl,
  updateProposalItem,
} from "../../api/proposalLibrary";
import ConfirmDelete from "../../components/ConfirmDelete";
import DataTable from "../../components/DataTable";
import Drawer, { useRequestDrawerClose } from "../../components/Drawer";
import EmptyState from "../../components/EmptyState";
import Field from "../../components/Field";
import FileUpload from "../../components/FileUpload";
import FilterBar from "../../components/FilterBar";
import FilterChips from "../../components/FilterChips";
import FormRow from "../../components/FormRow";
import Pagination from "../../components/Pagination";
import SearchInput from "../../components/SearchInput";
import TagChip from "../../components/TagChip";
import useHandoffNotice from "../../hooks/useHandoffNotice";
import usePageRecovery, { wholePage } from "../../hooks/usePageRecovery";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { formatFileSize } from "../../lib/format";

const PAGE_SIZE = 20;
const MAX_BYTES = 20 * 1024 * 1024; // matches the backend's upload limit
const dash = (value) => value || "—";
const FILTER_SCHEMA = { q: "", part: "", section: "", type: "", images: "", sort: "section", page: 1 };

const EMPTY_FORM = {
  section_number: "",
  section_title: "",
  subsection: "",
  point_number: "",
  title: "",
  requirement_text: "",
  response_text: "",
};

const formFrom = (item) =>
  Object.fromEntries(Object.keys(EMPTY_FORM).map((key) => [key, item?.[key] ?? EMPTY_FORM[key]]));

/** The write-up as it reads: requirement, then the answer, with a copy button. */
function Reading({ item }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(item.response_text || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }
  return (
    <section className="drawer-preview">
      <p className="muted">
        Section {dash(item.section_number)} · {dash(item.section_title)}
        {item.subsection ? ` › ${item.subsection}` : ""}
        {item.point_number !== null && item.point_number !== undefined ? ` · point ${item.point_number}` : ""}
      </p>
      <h3>Requirement</h3>
      <p style={{ whiteSpace: "pre-wrap" }}>{dash(item.requirement_text)}</p>
      <h3>Response</h3>
      <p style={{ whiteSpace: "pre-wrap" }}>{dash(item.response_text)}</p>
      <div className="preview-toolbar">
        <button type="button" className="btn btn-sm" onClick={copy} disabled={!item.response_text}>
          {copied ? "Copied" : "Copy response"}
        </button>
        {item.has_file && (
          <a className="btn btn-sm" href={proposalFileUrl(item.id)}>
            Download original ({item.file_type})
          </a>
        )}
      </div>
      <p className="muted">
        {item.image_count
          ? `The original contains ${item.image_count} picture${item.image_count === 1 ? "" : "s"}, which are not shown here; download it to see them.`
          : "The original contains no pictures."}
      </p>
    </section>
  );
}

function ItemForm({ item, onSaved, onDirtyChange }) {
  const editing = Boolean(item);
  const initial = useMemo(() => formFrom(item), [item]);
  const [form, setForm] = useState(initial);
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const cancel = useRequestDrawerClose();

  const dirty = file !== null || JSON.stringify(form) !== JSON.stringify(initial);
  useEffect(() => {
    if (onDirtyChange) onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  const change = (e) => setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));

  function pick(e) {
    const picked = e.target.files[0] || null;
    let problem = null;
    if (picked && !/\.(docx|rtf)$/i.test(picked.name)) problem = "Please choose a Word (.docx) or RTF (.rtf) file.";
    else if (picked && picked.size === 0) problem = `"${picked.name}" is empty (0 bytes).`;
    else if (picked && picked.size > MAX_BYTES) problem = `"${picked.name}" is over the 20 MB limit.`;
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
    if (!form.section_number.trim()) return setError("Please fill in the section number.");
    if (!editing && !file) return setFileError("Please choose the write-up file.");
    const point = form.point_number === "" ? null : Number(form.point_number);
    if (point !== null && (!Number.isInteger(point) || point < 0)) return setError("Point number must be a whole number.");
    setBusy(true);
    try {
      const saved = editing
        ? await updateProposalItem(item.id, { ...form, point_number: point })
        : await createProposalItem(
            { section_number: form.section_number, section_title: form.section_title, subsection: form.subsection, point_number: point },
            file,
          );
      onSaved(saved, editing);
    } catch (err) {
      setError(err.message || "Something went wrong while saving.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      {error && <div className="alert alert-error">{error}</div>}
      <FormRow>
        <Field label="Section number *" htmlFor="section_number" required>
          <input id="section_number" name="section_number" type="text" value={form.section_number} onChange={change} placeholder="e.g. 3.3.7" />
        </Field>
        <Field label="Section title" htmlFor="section_title">
          <input id="section_title" name="section_title" type="text" value={form.section_title} onChange={change} />
        </Field>
      </FormRow>
      <FormRow>
        <Field label="Subsection" htmlFor="subsection">
          <input id="subsection" name="subsection" type="text" value={form.subsection} onChange={change} />
        </Field>
        <Field label="Point number" htmlFor="point_number">
          <input id="point_number" name="point_number" type="number" min="0" value={form.point_number} onChange={change} />
        </Field>
      </FormRow>
      {editing && (
        <>
          <Field label="Title" htmlFor="title">
            <input id="title" name="title" type="text" value={form.title} onChange={change} />
          </Field>
          <Field label="Requirement" htmlFor="requirement_text">
            <textarea id="requirement_text" name="requirement_text" rows={3} value={form.requirement_text} onChange={change} />
          </Field>
          <Field label="Response" htmlFor="response_text">
            <textarea id="response_text" name="response_text" rows={8} value={form.response_text} onChange={change} />
          </Field>
        </>
      )}
      {!editing && (
        <FileUpload
          id="document_file"
          name="document_file"
          label="Write-up file (.docx or .rtf) *"
          hint={file ? `Selected: ${file.name} (${formatFileSize(file.size)}). The text is read from the file.` : "The requirement and response are read from the file's text."}
          error={fileError}
          accept=".docx,.rtf"
          onChange={pick}
        />
      )}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Saving..." : editing ? "Save changes" : "Add write-up"}
        </button>
        <button type="button" className="btn" onClick={cancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function ProposalLibraryPage() {
  const { values, setValues } = useQueryParams(FILTER_SCHEMA);
  const [refreshKey, setRefreshKey] = useState(0);
  const [drawer, setDrawer] = useState(null); // null | { item } (item null = new)
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useHandoffNotice();
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [opening, setOpening] = useState(false);
  const reportDirty = useCallback((value) => setDirty(value), []);

  const page = wholePage(values.page);
  const load = useCallback(
    () =>
      listProposalItems({
        q: values.q,
        part: values.part,
        section: values.section,
        fileType: values.type,
        hasImages: values.images,
        sort: values.sort,
        page,
        pageSize: PAGE_SIZE,
      }),
    [values, page, refreshKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const items = useResource(load, { initialData: { items: [], total: 0 } });
  const movingPage = usePageRecovery({
    rawPage: values.page,
    page,
    resource: items,
    pageSize: PAGE_SIZE,
    setPage: (p) => setValues({ page: p }),
  });
  const loadSections = useCallback(() => listProposalSections(), [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const sections = useResource(loadSections, { initialData: [] });
  const rows = items.data.items;
  const total = items.data.total;

  const parts = useMemo(() => {
    const counts = new Map();
    for (const s of sections.data) {
      const part = (s.section_number || "").match(/^\d+/)?.[0];
      if (part) counts.set(part, (counts.get(part) || 0) + s.count);
    }
    return [...counts.entries()].sort((a, b) => Number(a[0]) - Number(b[0]));
  }, [sections.data]);
  const visibleSections = values.part
    ? sections.data.filter((s) => (s.section_number || "").match(/^\d+/)?.[0] === values.part)
    : sections.data;

  const setFilter = (patch) => setValues({ ...patch, page: 1 });
  const clearFilters = () => setFilter({ q: "", part: "", section: "", type: "", images: "" });
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

  async function openItem(row) {
    setOpening(true);
    try {
      const full = await getProposalItem(row.id);
      setDirty(false);
      setDrawer({ item: full });
    } catch (err) {
      setNotice(err.message || "Could not open the write-up.");
    } finally {
      setOpening(false);
    }
  }

  function closeDrawer() {
    setDrawer(null);
    setDirty(false);
  }

  function saved(record, wasEditing) {
    setRefreshKey((k) => k + 1);
    closeDrawer();
    setNotice(wasEditing ? "Write-up saved." : "Write-up added.");
  }

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProposalItem(pendingDelete.id);
      setPendingDelete(null);
      setNotice("Write-up deleted.");
      if (rows.length === 1 && page > 1) setValues({ page: page - 1 });
      else items.reload();
      sections.reload();
    } catch (err) {
      setDeleteError(err.message || "Could not delete this write-up.");
    } finally {
      setDeleting(false);
    }
  }

  const chips = [
    values.q && { key: "q", label: `Search: ${values.q}`, onRemove: () => setFilter({ q: "" }) },
    values.part && { key: "part", label: `Part: ${values.part}`, onRemove: () => setFilter({ part: "", section: "" }) },
    values.section && { key: "section", label: `Section: ${values.section}`, onRemove: () => setFilter({ section: "" }) },
    values.type && { key: "type", label: `File: ${values.type}`, onRemove: () => setFilter({ type: "" }) },
    values.images && { key: "images", label: values.images === "true" ? "With pictures" : "Without pictures", onRemove: () => setFilter({ images: "" }) },
  ].filter(Boolean);
  const filtering = chips.length > 0;

  const columns = [
    { key: "section", header: sortHeader("section", "Section"), className: "cell-tight", render: (r) => dash(r.section_number) },
    {
      key: "title",
      header: sortHeader("title", "Requirement"),
      className: "cell-name",
      render: (r) => (
        <>
          {r.title}
          <div className="muted">
            {[r.section_title, r.subsection, r.point_number !== null && `point ${r.point_number}`].filter(Boolean).join(" › ")}
          </div>
        </>
      ),
    },
    { key: "word_count", header: sortHeader("word_count", "Words"), className: "cell-tight", render: (r) => r.word_count },
    { key: "image_count", header: "Pictures", className: "cell-tight", render: (r) => r.image_count || "—" },
    { key: "file_type", header: "File", className: "cell-tight", render: (r) => <TagChip value={(r.file_type || "").toUpperCase()} /> },
    {
      key: "actions",
      header: "Actions",
      className: "cell-tight",
      render: (r) => (
        <div className="row-actions">
          {r.has_file && (
            <a className="btn btn-sm" href={proposalFileUrl(r.id)}>
              Download
            </a>
          )}
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
              id="library-search"
              value={values.q}
              onChange={(q) => setFilter({ q })}
              placeholder="Requirement, response text, section number or title"
            />
          }
        >
          <Field label="Part" htmlFor="lib-filter-part">
            <select id="lib-filter-part" value={values.part} onChange={(e) => setFilter({ part: e.target.value, section: "" })}>
              <option value="">All parts</option>
              {parts.map(([part, count]) => (
                <option key={part} value={part}>Section {part}.x ({count})</option>
              ))}
            </select>
          </Field>
          <Field label="Section" htmlFor="lib-filter-section">
            <select id="lib-filter-section" value={values.section} onChange={(e) => setFilter({ section: e.target.value })}>
              <option value="">All sections</option>
              {visibleSections.map((s) => (
                <option key={`${s.section_number}|${s.section_title}`} value={s.section_number || ""}>
                  {s.section_number} {s.section_title} ({s.count})
                </option>
              ))}
              {values.section && !visibleSections.some((s) => s.section_number === values.section) && (
                <option value={values.section}>{values.section}</option>
              )}
            </select>
          </Field>
          <Field label="File type" htmlFor="lib-filter-type">
            <select id="lib-filter-type" value={values.type} onChange={(e) => setFilter({ type: e.target.value })}>
              <option value="">All</option>
              <option value="docx">Word (.docx)</option>
              <option value="rtf">RTF (.rtf)</option>
            </select>
          </Field>
          <Field label="Pictures" htmlFor="lib-filter-images">
            <select id="lib-filter-images" value={values.images} onChange={(e) => setFilter({ images: e.target.value })}>
              <option value="">Any</option>
              <option value="true">With pictures</option>
              <option value="false">Without pictures</option>
            </select>
          </Field>
        </FilterBar>

        <section className="card list-panel">
          <div className="list-head">
            <h2>Write-ups {!items.loading && !items.error && `(${total})`}</h2>
            <button type="button" className="btn btn-primary" onClick={() => { setDirty(false); setDrawer({ item: null }); }}>
              + Add write-up
            </button>
          </div>
          <FilterChips chips={chips} />
          <DataTable
            columns={columns}
            rows={rows}
            loading={items.loading || movingPage || opening}
            error={items.error}
            onRetry={items.reload}
            rowClassName={(r) => (drawer?.item?.id === r.id ? "row-editing" : undefined)}
            onRowClick={openItem}
            empty={
              filtering ? (
                <EmptyState
                  message="No write-ups match your filters."
                  action={<button type="button" className="btn btn-sm" onClick={clearFilters}>Clear all filters</button>}
                />
              ) : (
                <EmptyState message="The library is empty. Write-ups are added here or imported in bulk." />
              )
            }
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={(p) => setValues({ page: p })} />
        </section>
      </div>

      {drawer && (
        <Drawer
          title={drawer.item ? `${drawer.item.section_number || ""} ${drawer.item.section_title || "Write-up"}`.trim() : "Add write-up"}
          onClose={closeDrawer}
          dirty={dirty}
        >
          {drawer.item && <Reading item={drawer.item} />}
          <ItemForm key={drawer.item?.id || "new"} item={drawer.item} onSaved={saved} onDirtyChange={reportDirty} />
        </Drawer>
      )}

      {pendingDelete && (
        <ConfirmDelete
          title="Delete this write-up?"
          busy={deleting}
          error={deleteError}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        >
          <p className="muted">
            {pendingDelete.section_number} {pendingDelete.section_title}
            {pendingDelete.point_number !== null ? ` · point ${pendingDelete.point_number}` : ""}
          </p>
          <p className="muted">
            The record and its stored file ({pendingDelete.file_name || "no file"}) will be permanently removed. This cannot be undone.
          </p>
        </ConfirmDelete>
      )}
    </>
  );
}

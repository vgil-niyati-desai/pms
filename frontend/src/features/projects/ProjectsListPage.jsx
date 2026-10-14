import { useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import FilterBar from "../../components/FilterBar";
import FilterMenu from "../../components/FilterMenu";
import FilterChips from "../../components/FilterChips";
import SearchInput from "../../components/SearchInput";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import CheckboxGroup from "../../components/CheckboxGroup";
import TagInput from "../../components/TagInput";
import TagChip from "../../components/TagChip";
import StatusPill from "../../components/StatusPill";
import Pagination from "../../components/Pagination";
import useHandoffNotice from "../../hooks/useHandoffNotice";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import usePageRecovery, { wholePage } from "../../hooks/usePageRecovery";
import { listProjects, listClients, listTags, PROJECT_STATUSES } from "../../api/projects";
import { EVIDENCE_TYPES, formatAmount, formatPeriod, formatTimestamp } from "./projectFields";
import EvidenceStrip from "./EvidenceStrip";
import { paths } from "../../app/routes";

const PAGE_SIZE = 10;

// Module-level so the object identity stays stable across renders.
const FILTER_SCHEMA = {
  q: "",
  client: "",
  tags: [],
  tagMode: "any",
  status: "",
  has: [],
  endDateFrom: "",
  endDateTo: "",
  minValue: "",
  maxValue: "",
  sort: "-updated_at",
  page: 1,
};

export default function ProjectsListPage() {
  const navigate = useNavigate();
  const { values, setValues, clear } = useQueryParams(FILTER_SCHEMA);
  // Handed over by the project form after a delete: the record it confirms
  // no longer exists, so this is the only screen left to say it on.
  const [notice, setNotice] = useHandoffNotice();

  // Each project arrives carrying the document types it holds, resolved by
  // the server for the projects on this page. The screen used to download
  // the entire document log and work that out for itself.
  const page = wholePage(values.page);
  const loadProjects = useCallback(
    () => listProjects({ ...values, page, pageSize: PAGE_SIZE }),
    [values, page],
  );
  const projects = useResource(loadProjects, { initialData: { items: [], total: 0 } });
  // The page the URL names, as one that exists; see usePageRecovery.
  const movingPage = usePageRecovery({
    rawPage: values.page,
    page,
    resource: projects,
    pageSize: PAGE_SIZE,
    setPage: (page) => setValues({ page }),
  });

  const loadClients = useCallback(() => listClients(), []);
  const clients = useResource(loadClients, { initialData: [] });

  const loadTags = useCallback(() => listTags(), []);
  const tags = useResource(loadTags, { initialData: [] });

  function setFilter(patch) {
    // Any filter change returns to page one; otherwise a narrower result set
    // leaves you stranded past the end of the list.
    setValues({ ...patch, page: 1 });
  }

  function toggleSort(key) {
    setValues({ sort: values.sort === key ? `-${key}` : key, page: 1 });
  }

  function sortHeader(key, label) {
    const descending = values.sort === `-${key}`;
    const active = descending || values.sort === key;
    return (
      <button
        type="button"
        className={active ? "sort-header sort-active" : "sort-header"}
        onClick={() => toggleSort(key)}
      >
        {label}
        <span aria-hidden="true">{active ? (descending ? " ▼" : " ▲") : ""}</span>
      </button>
    );
  }

  const chips = [
    values.q && { key: "q", label: `Search: ${values.q}`, onRemove: () => setFilter({ q: "" }) },
    values.client && {
      key: "client",
      label: `Client: ${values.client}`,
      onRemove: () => setFilter({ client: "" }),
    },
    values.status && {
      key: "status",
      label: `Status: ${values.status}`,
      onRemove: () => setFilter({ status: "" }),
    },
    ...values.tags.map((tag) => ({
      key: `tag-${tag}`,
      label: `Tag: ${tag}`,
      onRemove: () => setFilter({ tags: values.tags.filter((t) => t !== tag) }),
    })),
    ...values.has.map((type) => ({
      key: `has-${type}`,
      label: `Has ${type}`,
      onRemove: () => setFilter({ has: values.has.filter((t) => t !== type) }),
    })),
    (values.endDateFrom || values.endDateTo) && {
      key: "endDate",
      // Named as the end date rather than "completed": the bound is on the
      // date recorded, which on an ongoing project is when it is due to end.
      label: `End date: ${values.endDateFrom || "any"} to ${values.endDateTo || "any"}`,
      onRemove: () => setFilter({ endDateFrom: "", endDateTo: "" }),
    },
    (values.minValue !== "" || values.maxValue !== "") && {
      key: "value",
      label: `Value: ${values.minValue || "0"} to ${values.maxValue || "any"}`,
      onRemove: () => setFilter({ minValue: "", maxValue: "" }),
    },
  ].filter(Boolean);

  const columns = [
    {
      key: "title",
      header: sortHeader("title", "Project title"),
      className: "cell-name",
    },
    { key: "client_name", header: sortHeader("client_name", "Client") },
    {
      key: "status",
      header: sortHeader("status", "Status"),
      className: "cell-tight",
      render: (project) => <StatusPill value={project.status} />,
    },
    {
      key: "contract_value",
      header: sortHeader("contract_value", "Value"),
      // Read as money, the way a tender's value already is. Sorting is the
      // server's and is unaffected: it parses the stored text itself.
      render: (project) => formatAmount(project.contract_value),
    },
    {
      key: "period",
      header: "Period",
      render: (project) => formatPeriod(project.start_date, project.end_date),
    },
    {
      key: "documents",
      header: "Documents",
      // The ratio leads the pills so whether a project is ready to cite in a
      // tender reads at a glance, without counting checkmarks across a row.
      // Intersected with EVIDENCE_TYPES because document_types can carry any
      // type a linked document happens to have, not only the five evidence
      // types this count is about.
      render: (project) => {
        const heldTypes = project.document_types || [];
        const heldEvidence = EVIDENCE_TYPES.filter((type) => heldTypes.includes(type));
        return (
          <>
            <div className="muted evidence-count" style={{ marginBottom: 4 }}>
              {heldEvidence.length}/{EVIDENCE_TYPES.length} evidence
            </div>
            <EvidenceStrip compact heldTypes={heldTypes} />
          </>
        );
      },
    },
    {
      key: "tags",
      header: "Tags",
      render: (project) =>
        project.tags.length ? (
          <div className="tag-list">
            {project.tags.map((tag) => (
              <TagChip key={tag} value={tag} />
            ))}
          </div>
        ) : (
          "—"
        ),
    },
    {
      key: "updated_at",
      header: sortHeader("updated_at", "Updated"),
      className: "cell-tight",
      render: (project) => formatTimestamp(project.updated_at),
    },
  ];

  const items = projects.data?.items ?? [];
  const total = projects.data?.total ?? 0;
  const filtering = chips.length > 0;

  return (
    <div className="list-layout">
      {notice && (
        <div className="alert alert-success notice-bar" role="status">
          <span>{notice}</span>
          <button
            type="button"
            className="notice-dismiss"
            onClick={() => setNotice(null)}
            aria-label="Dismiss this message"
          >
            &times;
          </button>
        </div>
      )}

      <FilterBar
        showClear={filtering}
        onClear={clear}
        search={
          <SearchInput
            id="project-search"
            value={values.q}
            onChange={(q) => setFilter({ q })}
            placeholder="Title, client, reference no."
          />
        }
      >
        <Field label="Client" htmlFor="filter-client">
          <select
            id="filter-client"
            value={values.client}
            onChange={(e) => setFilter({ client: e.target.value })}
          >
            <option value="">All clients</option>
            {clients.data.map((client) => (
              <option key={client} value={client}>
                {client}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Status" htmlFor="filter-status">
          <select
            id="filter-status"
            value={values.status}
            onChange={(e) => setFilter({ status: e.target.value })}
          >
            <option value="">Any status</option>
            {PROJECT_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </Field>

        {/* The two bounds a tender's experience clauses are written in --
            "each worth at least X", "completed in the last N years" -- so the
            projects that can answer one can be found without reading the
            list. Folded behind a trigger like the tender list's own ranges:
            a from/to pair is too wide for a line of the toolbar. */}
        <FilterMenu
          label="End date"
          count={values.endDateFrom || values.endDateTo ? 1 : 0}
        >
          <FormRow>
            <Field label="End date from" htmlFor="filter-end-from">
              <input
                id="filter-end-from"
                type="date"
                value={values.endDateFrom}
                onChange={(e) => setFilter({ endDateFrom: e.target.value })}
              />
            </Field>
            <Field label="to" htmlFor="filter-end-to">
              <input
                id="filter-end-to"
                type="date"
                value={values.endDateTo}
                onChange={(e) => setFilter({ endDateTo: e.target.value })}
              />
            </Field>
          </FormRow>
          <p className="muted hint">
            A project with no end date recorded is not in range either way.
          </p>
        </FilterMenu>

        <FilterMenu
          label="Value"
          count={values.minValue !== "" || values.maxValue !== "" ? 1 : 0}
        >
          <FormRow>
            <Field label="Value from" htmlFor="filter-min-value">
              <input
                id="filter-min-value"
                type="number"
                min="0"
                value={values.minValue}
                onChange={(e) => setFilter({ minValue: e.target.value })}
              />
            </Field>
            <Field label="to" htmlFor="filter-max-value">
              <input
                id="filter-max-value"
                type="number"
                min="0"
                value={values.maxValue}
                onChange={(e) => setFilter({ maxValue: e.target.value })}
              />
            </Field>
          </FormRow>
          <p className="muted hint">
            Contract values are typed as text, so one that cannot be read as a
            number — and one not recorded at all — is not in range either way.
          </p>
        </FilterMenu>

        <FilterMenu label="Has document" count={values.has.length}>
          <CheckboxGroup
            legend="Has document"
            options={EVIDENCE_TYPES}
            value={values.has}
            onChange={(has) => setFilter({ has })}
          />
        </FilterMenu>

        <FilterMenu label="Tags" count={values.tags.length}>
          <TagInput
            value={values.tags}
            suggestions={tags.data}
            onChange={(next) => setFilter({ tags: next })}
            hint="Show only projects carrying these tags."
          />

          {values.tags.length > 1 && (
            <Field label="Tag match" htmlFor="filter-tag-mode">
              <select
                id="filter-tag-mode"
                value={values.tagMode}
                onChange={(e) => setFilter({ tagMode: e.target.value })}
              >
                <option value="any">Any of these tags</option>
                <option value="all">All of these tags</option>
              </select>
            </Field>
          )}
        </FilterMenu>
      </FilterBar>

      <section className="card list-panel">
        <div className="list-head">
          <h2>Projects {!projects.loading && !projects.error && `(${total})`}</h2>
        </div>

        <FilterChips chips={chips} />

        <DataTable
          columns={columns}
          rows={items}
          loading={projects.loading || movingPage}
          error={projects.error}
          onRetry={projects.reload}
          onRowClick={(project) => navigate(paths.project(project.id))}
          empty={
            filtering ? (
              <EmptyState
                message="No projects match your filters."
                action={
                  <button type="button" className="btn btn-sm" onClick={clear}>
                    Clear all filters
                  </button>
                }
              />
            ) : (
              <EmptyState
                message="No projects yet."
                action={
                  <Link className="btn btn-sm" to={paths.newProject()}>
                    Create the first project
                  </Link>
                }
              />
            )
          }
        />

        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(page) => setValues({ page })}
        />
      </section>
    </div>
  );
}

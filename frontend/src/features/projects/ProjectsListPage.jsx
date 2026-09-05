import { useCallback, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import FilterBar from "../../components/FilterBar";
import FilterMenu from "../../components/FilterMenu";
import FilterChips from "../../components/FilterChips";
import SearchInput from "../../components/SearchInput";
import Field from "../../components/Field";
import CheckboxGroup from "../../components/CheckboxGroup";
import TagInput from "../../components/TagInput";
import TagChip from "../../components/TagChip";
import Pagination from "../../components/Pagination";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { listDocuments } from "../../api/documents";
import {
  listProjects,
  listClients,
  listTags,
  documentTypesFor,
  PROJECT_STATUSES,
} from "../../api/projects";
import {
  EVIDENCE_TYPES,
  dash,
  formatPeriod,
  formatTimestamp,
  documentTypeIndex,
} from "./projectFields";
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
  sort: "-updated_at",
  page: 1,
};

export default function ProjectsListPage() {
  const navigate = useNavigate();
  const { values, setValues, clear } = useQueryParams(FILTER_SCHEMA);

  // Document types live on the documents API. Both the has-document filter and
  // the evidence pills need them, so they are loaded once for the screen.
  const loadDocuments = useCallback(() => listDocuments(), []);
  const documents = useResource(loadDocuments, { initialData: [] });
  const documentTypeById = useMemo(() => documentTypeIndex(documents.data), [documents.data]);

  const loadProjects = useCallback(
    () => listProjects({ ...values, pageSize: PAGE_SIZE, documentTypeById }),
    [values, documentTypeById],
  );
  const projects = useResource(loadProjects, { initialData: { items: [], total: 0 } });

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
  ].filter(Boolean);

  const columns = [
    {
      key: "title",
      header: sortHeader("title", "Project title"),
      className: "cell-name",
    },
    { key: "client_name", header: sortHeader("client_name", "Client") },
    {
      key: "contract_value",
      header: sortHeader("contract_value", "Value"),
      render: (project) => dash(project.contract_value),
    },
    {
      key: "period",
      header: "Period",
      render: (project) => formatPeriod(project.start_date, project.end_date),
    },
    {
      key: "documents",
      header: "Documents",
      render: (project) => (
        <EvidenceStrip compact heldTypes={documentTypesFor(project, documentTypeById)} />
      ),
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
          <h2>Projects {!projects.loading && `(${total})`}</h2>
        </div>

        <FilterChips chips={chips} />

        {projects.error && <div className="alert alert-error">{projects.error}</div>}
        {documents.error && (
          <div className="alert alert-error">
            Documents could not be loaded ({documents.error}). The evidence column and the
            has-document filter stay empty until the backend is reachable.
          </div>
        )}

        <DataTable
          columns={columns}
          rows={items}
          loading={projects.loading}
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
          page={values.page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(page) => setValues({ page })}
        />
      </section>
    </div>
  );
}

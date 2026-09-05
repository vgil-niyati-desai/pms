import { useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import FilterBar from "../../components/FilterBar";
import FilterMenu from "../../components/FilterMenu";
import FilterChips from "../../components/FilterChips";
import SearchInput from "../../components/SearchInput";
import SegmentedControl from "../../components/SegmentedControl";
import StatusPill from "../../components/StatusPill";
import TagChip from "../../components/TagChip";
import TagInput from "../../components/TagInput";
import CheckboxGroup from "../../components/CheckboxGroup";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import Pagination from "../../components/Pagination";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import {
  listTenders,
  listAuthorities,
  listTags,
  TENDER_STATUSES,
  OPEN_STATUSES,
} from "../../api/tenders";
import { dash, formatAmount, formatTimestamp } from "../../lib/format";
import { deadlineUrgency } from "./tenderFields";
import { paths } from "../../app/routes";

const PAGE_SIZE = 10;

const VIEWS = [
  { key: "open", label: "Open" },
  { key: "all", label: "All" },
];

// Module-level so the object identity stays stable across renders.
const FILTER_SCHEMA = {
  view: "open",
  q: "",
  statuses: [],
  authority: "",
  tags: [],
  tagMode: "any",
  deadlineFrom: "",
  deadlineTo: "",
  minValue: "",
  maxValue: "",
  sort: "",
  page: 1,
};

export default function TendersListPage() {
  const navigate = useNavigate();
  const { values, setValues, clear } = useQueryParams(FILTER_SCHEMA);

  const openOnly = values.view === "open";
  // Open tenders are read by what is due next; everything else by what changed
  // last. An explicit sort in the URL wins over both.
  const sort = values.sort || (openOnly ? "submission_deadline" : "-updated_at");

  const loadTenders = useCallback(
    () => listTenders({ ...values, sort, openOnly, pageSize: PAGE_SIZE }),
    [values, sort, openOnly],
  );
  const tenders = useResource(loadTenders, { initialData: { items: [], total: 0 } });

  const loadAuthorities = useCallback(() => listAuthorities(), []);
  const authorities = useResource(loadAuthorities, { initialData: [] });

  const loadTags = useCallback(() => listTags(), []);
  const tags = useResource(loadTags, { initialData: [] });

  function setFilter(patch) {
    // Any filter change returns to page one; otherwise a narrower result set
    // leaves you stranded past the end of the list.
    setValues({ ...patch, page: 1 });
  }

  function toggleSort(key) {
    setValues({ sort: sort === key ? `-${key}` : key, page: 1 });
  }

  function sortHeader(key, label) {
    const descending = sort === `-${key}`;
    const active = descending || sort === key;
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
    values.authority && {
      key: "authority",
      label: `Authority: ${values.authority}`,
      onRemove: () => setFilter({ authority: "" }),
    },
    ...values.statuses.map((status) => ({
      key: `status-${status}`,
      label: `Status: ${status}`,
      onRemove: () => setFilter({ statuses: values.statuses.filter((s) => s !== status) }),
    })),
    ...values.tags.map((tag) => ({
      key: `tag-${tag}`,
      label: `Tag: ${tag}`,
      onRemove: () => setFilter({ tags: values.tags.filter((t) => t !== tag) }),
    })),
    (values.deadlineFrom || values.deadlineTo) && {
      key: "deadline",
      label: `Deadline: ${values.deadlineFrom || "any"} to ${values.deadlineTo || "any"}`,
      onRemove: () => setFilter({ deadlineFrom: "", deadlineTo: "" }),
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
      header: sortHeader("title", "Tender"),
      className: "cell-name",
    },
    { key: "issuing_authority", header: sortHeader("issuing_authority", "Authority") },
    {
      key: "reference_number",
      header: "Reference no.",
      render: (tender) => dash(tender.reference_number),
    },
    {
      key: "estimated_value",
      header: sortHeader("estimated_value", "Value"),
      render: (tender) => formatAmount(tender.estimated_value),
    },
    {
      key: "submission_deadline",
      header: sortHeader("submission_deadline", "Deadline"),
      className: "cell-tight",
      render: (tender) => {
        const urgency =
          OPEN_STATUSES.includes(tender.status) && deadlineUrgency(tender.submission_deadline);
        return (
          <>
            {dash(tender.submission_deadline)}
            {urgency && <span className="deadline-flag">{urgency}</span>}
          </>
        );
      },
    },
    {
      key: "status",
      header: sortHeader("status", "Status"),
      className: "cell-tight",
      render: (tender) => <StatusPill value={tender.status} />,
    },
    {
      key: "tags",
      header: "Tags",
      render: (tender) =>
        tender.tags.length ? (
          <div className="tag-list">
            {tender.tags.map((tag) => (
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
      render: (tender) => formatTimestamp(tender.updated_at),
    },
  ];

  const items = tenders.data?.items ?? [];
  const total = tenders.data?.total ?? 0;
  const filtering = chips.length > 0;

  return (
    <>
      <SegmentedControl
        label="Choose a view"
        options={VIEWS}
        value={values.view}
        onChange={(view) => setValues({ view, sort: "", page: 1 })}
      />

      <div className="list-layout">
        <FilterBar
          showClear={filtering}
          onClear={clear}
          search={
            <SearchInput
              id="tender-search"
              value={values.q}
              onChange={(q) => setFilter({ q })}
              placeholder="Title, reference no., authority"
            />
          }
        >
          <Field label="Issuing authority" htmlFor="filter-authority">
            <select
              id="filter-authority"
              value={values.authority}
              onChange={(e) => setFilter({ authority: e.target.value })}
            >
              <option value="">All authorities</option>
              {authorities.data.map((authority) => (
                <option key={authority} value={authority}>
                  {authority}
                </option>
              ))}
            </select>
          </Field>

          <FilterMenu label="Status" count={values.statuses.length}>
            <CheckboxGroup
              legend="Status"
              options={openOnly ? OPEN_STATUSES : TENDER_STATUSES}
              value={values.statuses}
              onChange={(statuses) => setFilter({ statuses })}
            />
          </FilterMenu>

          <FilterMenu label="Deadline" count={values.deadlineFrom || values.deadlineTo ? 1 : 0}>
            <FormRow>
              <Field label="Deadline from" htmlFor="filter-deadline-from">
                <input
                  id="filter-deadline-from"
                  type="date"
                  value={values.deadlineFrom}
                  onChange={(e) => setFilter({ deadlineFrom: e.target.value })}
                />
              </Field>
              <Field label="to" htmlFor="filter-deadline-to">
                <input
                  id="filter-deadline-to"
                  type="date"
                  value={values.deadlineTo}
                  onChange={(e) => setFilter({ deadlineTo: e.target.value })}
                />
              </Field>
            </FormRow>
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
          </FilterMenu>

          <FilterMenu label="Tags" count={values.tags.length}>
            <TagInput
              value={values.tags}
              suggestions={tags.data}
              onChange={(next) => setFilter({ tags: next })}
              hint="Show only tenders carrying these tags."
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
            <h2>
              {openOnly ? "Open tenders" : "All tenders"} {!tenders.loading && `(${total})`}
            </h2>
          </div>

          <FilterChips chips={chips} />

          {tenders.error && <div className="alert alert-error">{tenders.error}</div>}

          <DataTable
            columns={columns}
            rows={items}
            loading={tenders.loading}
            onRowClick={(tender) => navigate(paths.tender(tender.id))}
            empty={
              filtering ? (
                <EmptyState
                  message="No tenders match your filters."
                  action={
                    <button type="button" className="btn btn-sm" onClick={clear}>
                      Clear all filters
                    </button>
                  }
                />
              ) : openOnly ? (
                <EmptyState
                  message="No open tenders. Anything already submitted, won or lost is under All."
                  action={
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => setValues({ view: "all", sort: "", page: 1 })}
                    >
                      Show all tenders
                    </button>
                  }
                />
              ) : (
                <EmptyState
                  message="No tenders yet."
                  action={
                    <Link className="btn btn-sm" to={paths.newTender()}>
                      Add the first tender
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
    </>
  );
}

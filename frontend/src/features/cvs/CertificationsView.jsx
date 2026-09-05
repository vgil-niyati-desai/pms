import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import FilterBar from "../../components/FilterBar";
import FilterChips from "../../components/FilterChips";
import SearchInput from "../../components/SearchInput";
import StatusPill from "../../components/StatusPill";
import Field from "../../components/Field";
import FileViewLink from "../../components/FileViewLink";
import Pagination from "../../components/Pagination";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import {
  listCertifications,
  listCertificationNames,
  listIssuingBodies,
  CERTIFICATION_STATUSES,
} from "../../api/employees";
import { dash } from "../../lib/format";
import { paths } from "../../app/routes";

const PAGE_SIZE = 10;

// Its own keys so switching views does not carry the employee filters across.
const FILTER_SCHEMA = {
  certQ: "",
  certName: "",
  issuingBody: "",
  certStatus: "",
  certSort: "name",
  certPage: 1,
};

/**
 * Every certification held by anyone, flattened.
 *
 * "Who holds a PMP?" is a tender-qualification question, and answering it by
 * opening profiles one at a time does not scale — so it gets its own view
 * rather than living only inside each employee.
 */
export default function CertificationsView() {
  const navigate = useNavigate();
  const { values, setValues, clear } = useQueryParams(FILTER_SCHEMA);

  const loadCertifications = useCallback(
    () =>
      listCertifications({
        q: values.certQ,
        name: values.certName,
        issuingBody: values.issuingBody,
        status: values.certStatus,
        sort: values.certSort,
        page: values.certPage,
        pageSize: PAGE_SIZE,
      }),
    [values],
  );
  const certifications = useResource(loadCertifications, { initialData: { items: [], total: 0 } });

  const loadNames = useCallback(() => listCertificationNames(), []);
  const names = useResource(loadNames, { initialData: [] });

  const loadBodies = useCallback(() => listIssuingBodies(), []);
  const bodies = useResource(loadBodies, { initialData: [] });

  function setFilter(patch) {
    setValues({ ...patch, certPage: 1 });
  }

  function toggleSort(key) {
    setValues({ certSort: values.certSort === key ? `-${key}` : key, certPage: 1 });
  }

  function sortHeader(key, label) {
    const descending = values.certSort === `-${key}`;
    const active = descending || values.certSort === key;
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
    values.certQ && {
      key: "certQ",
      label: `Search: ${values.certQ}`,
      onRemove: () => setFilter({ certQ: "" }),
    },
    values.certName && {
      key: "certName",
      label: `Certificate: ${values.certName}`,
      onRemove: () => setFilter({ certName: "" }),
    },
    values.issuingBody && {
      key: "issuingBody",
      label: `Issued by: ${values.issuingBody}`,
      onRemove: () => setFilter({ issuingBody: "" }),
    },
    values.certStatus && {
      key: "certStatus",
      label: `Status: ${values.certStatus}`,
      onRemove: () => setFilter({ certStatus: "" }),
    },
  ].filter(Boolean);

  const columns = [
    { key: "name", header: sortHeader("name", "Certificate"), className: "cell-name" },
    {
      key: "issuing_body",
      header: sortHeader("issuing_body", "Issued by"),
      render: (row) => dash(row.issuing_body),
    },
    {
      key: "employee_name",
      header: sortHeader("employee_name", "Held by"),
    },
    {
      key: "certificate_number",
      header: "Certificate no.",
      render: (row) => dash(row.certificate_number),
    },
    {
      key: "issue_date",
      header: sortHeader("issue_date", "Issued"),
      className: "cell-tight",
      render: (row) => dash(row.issue_date),
    },
    {
      key: "expiry_date",
      header: sortHeader("expiry_date", "Expires"),
      className: "cell-tight",
      render: (row) => (
        <>
          {dash(row.expiry_date)} <StatusPill value={row.status} />
        </>
      ),
    },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      render: (row) => (
        <FileViewLink documentId={row.document_id} fileName={row.file_name} title={row.name} />
      ),
    },
  ];

  const items = certifications.data?.items ?? [];
  const total = certifications.data?.total ?? 0;
  const filtering = chips.length > 0;

  return (
    <div className="list-layout">
      <FilterBar
        showClear={filtering}
        onClear={clear}
        search={
          <SearchInput
            id="certification-search"
            value={values.certQ}
            onChange={(certQ) => setFilter({ certQ })}
            placeholder="Certificate, issuer, holder"
          />
        }
      >
        <Field label="Certificate" htmlFor="filter-cert-name">
          <select
            id="filter-cert-name"
            value={values.certName}
            onChange={(e) => setFilter({ certName: e.target.value })}
          >
            <option value="">All certificates</option>
            {names.data.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Issuing body" htmlFor="filter-issuing-body">
          <select
            id="filter-issuing-body"
            value={values.issuingBody}
            onChange={(e) => setFilter({ issuingBody: e.target.value })}
          >
            <option value="">All issuers</option>
            {bodies.data.map((body) => (
              <option key={body} value={body}>
                {body}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Validity" htmlFor="filter-cert-status">
          <select
            id="filter-cert-status"
            value={values.certStatus}
            onChange={(e) => setFilter({ certStatus: e.target.value })}
          >
            <option value="">Any</option>
            {CERTIFICATION_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </Field>
      </FilterBar>

      <section className="card list-panel">
        <div className="list-head">
          <h2>Certifications {!certifications.loading && `(${total})`}</h2>
        </div>

        <FilterChips chips={chips} />

        {certifications.error && <div className="alert alert-error">{certifications.error}</div>}

        <DataTable
          columns={columns}
          rows={items}
          loading={certifications.loading}
          onRowClick={(row) => navigate(paths.employee(row.employee_id))}
          empty={
            filtering ? (
              <EmptyState
                message="No certifications match your filters."
                action={
                  <button type="button" className="btn btn-sm" onClick={clear}>
                    Clear all filters
                  </button>
                }
              />
            ) : (
              <EmptyState message="No certifications recorded yet. Add them from an employee's profile." />
            )
          }
        />

        <Pagination
          page={values.certPage}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(certPage) => setValues({ certPage })}
        />
      </section>
    </div>
  );
}

import { useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import FilterBar from "../../components/FilterBar";
import FilterMenu from "../../components/FilterMenu";
import FilterChips from "../../components/FilterChips";
import SearchInput from "../../components/SearchInput";
import SegmentedControl from "../../components/SegmentedControl";
import Field from "../../components/Field";
import FormRow from "../../components/FormRow";
import CheckboxGroup from "../../components/CheckboxGroup";
import Pagination from "../../components/Pagination";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import {
  listEmployees,
  listDesignations,
  listDepartments,
  listCertificationNames,
} from "../../api/employees";
import { dash, formatTimestamp } from "../../lib/format";
import { latestCvDate, certificationSummary } from "./employeeFields";
import CertificationsView from "./CertificationsView";
import { paths } from "../../app/routes";

const PAGE_SIZE = 10;

const VIEWS = [
  { key: "employees", label: "Employees" },
  { key: "certifications", label: "Certifications" },
];

// Module-level so the object identity stays stable across renders.
const FILTER_SCHEMA = {
  view: "employees",
  q: "",
  designation: "",
  department: "",
  certifications: [],
  minExperience: "",
  maxExperience: "",
  sort: "-updated_at",
  page: 1,
};

export default function EmployeesListPage() {
  const navigate = useNavigate();
  const { values, setValues, clear } = useQueryParams(FILTER_SCHEMA);

  const loadEmployees = useCallback(
    () => listEmployees({ ...values, pageSize: PAGE_SIZE }),
    [values],
  );
  const employees = useResource(loadEmployees, { initialData: { items: [], total: 0 } });

  const loadDesignations = useCallback(() => listDesignations(), []);
  const designations = useResource(loadDesignations, { initialData: [] });

  const loadDepartments = useCallback(() => listDepartments(), []);
  const departments = useResource(loadDepartments, { initialData: [] });

  const loadCertificationNames = useCallback(() => listCertificationNames(), []);
  const certificationNames = useResource(loadCertificationNames, { initialData: [] });

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
    values.designation && {
      key: "designation",
      label: `Designation: ${values.designation}`,
      onRemove: () => setFilter({ designation: "" }),
    },
    values.department && {
      key: "department",
      label: `Department: ${values.department}`,
      onRemove: () => setFilter({ department: "" }),
    },
    ...values.certifications.map((name) => ({
      key: `certification-${name}`,
      label: `Holds: ${name}`,
      onRemove: () => setFilter({
        certifications: values.certifications.filter((held) => held !== name),
      }),
    })),
    (values.minExperience !== "" || values.maxExperience !== "") && {
      key: "experience",
      label: `Experience: ${values.minExperience || "0"}–${values.maxExperience || "any"} yrs`,
      onRemove: () => setFilter({ minExperience: "", maxExperience: "" }),
    },
  ].filter(Boolean);

  const columns = [
    {
      key: "full_name",
      header: sortHeader("full_name", "Name"),
      className: "cell-name",
    },
    {
      key: "designation",
      header: sortHeader("designation", "Designation"),
      render: (employee) => dash(employee.designation),
    },
    {
      key: "department",
      header: sortHeader("department", "Department"),
      render: (employee) => dash(employee.department),
    },
    {
      key: "experience_years",
      header: sortHeader("experience_years", "Experience"),
      className: "cell-tight",
      render: (employee) =>
        // dash() covers "" and the nulls open-ended records may hold.
        dash(employee.experience_years) === "—" ? "—" : `${employee.experience_years} yrs`,
    },
    {
      key: "cvs",
      header: "CVs",
      render: (employee) =>
        employee.cvs.length === 0 ? (
          <span className="muted">None</span>
        ) : (
          <>
            {employee.cvs.length}
            <span className="muted"> · latest {latestCvDate(employee)}</span>
          </>
        ),
    },
    {
      key: "certifications",
      header: "Certifications",
      render: (employee) => certificationSummary(employee),
    },
    {
      key: "updated_at",
      header: sortHeader("updated_at", "Updated"),
      className: "cell-tight",
      render: (employee) => formatTimestamp(employee.updated_at),
    },
  ];

  const items = employees.data?.items ?? [];
  const total = employees.data?.total ?? 0;
  const filtering = chips.length > 0;

  return (
    <>
      <SegmentedControl
        label="Choose a view"
        options={VIEWS}
        value={values.view}
        onChange={(view) => setValues({ view, page: 1 })}
      />

      {values.view === "certifications" ? (
        <CertificationsView />
      ) : (
        <div className="list-layout">
          <FilterBar
            showClear={filtering}
            onClear={clear}
            search={
              <SearchInput
                id="employee-search"
                value={values.q}
                onChange={(q) => setFilter({ q })}
                placeholder="Name, designation, qualification"
              />
            }
          >
            <Field label="Designation" htmlFor="filter-designation">
              <select
                id="filter-designation"
                value={values.designation}
                onChange={(e) => setFilter({ designation: e.target.value })}
              >
                <option value="">All designations</option>
                {designations.data.map((designation) => (
                  <option key={designation} value={designation}>
                    {designation}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Department" htmlFor="filter-department">
              <select
                id="filter-department"
                value={values.department}
                onChange={(e) => setFilter({ department: e.target.value })}
              >
                <option value="">All departments</option>
                {departments.data.map((department) => (
                  <option key={department} value={department}>
                    {department}
                  </option>
                ))}
              </select>
            </Field>

            {certificationNames.data.length > 0 && (
              <FilterMenu label="Certification held" count={values.certifications.length}>
                <CheckboxGroup
                  legend="Certification held"
                  options={certificationNames.data}
                  value={values.certifications}
                  onChange={(certifications) => setFilter({ certifications })}
                />
              </FilterMenu>
            )}

            <FilterMenu
              label="Experience"
              count={values.minExperience !== "" || values.maxExperience !== "" ? 1 : 0}
            >
              <FormRow>
                <Field label="Experience from" htmlFor="filter-min-exp">
                  <input
                    id="filter-min-exp"
                    type="number"
                    min="0"
                    value={values.minExperience}
                    onChange={(e) => setFilter({ minExperience: e.target.value })}
                    placeholder="yrs"
                  />
                </Field>
                <Field label="to" htmlFor="filter-max-exp">
                  <input
                    id="filter-max-exp"
                    type="number"
                    min="0"
                    value={values.maxExperience}
                    onChange={(e) => setFilter({ maxExperience: e.target.value })}
                    placeholder="yrs"
                  />
                </Field>
              </FormRow>
            </FilterMenu>
          </FilterBar>

          <section className="card list-panel">
            <div className="list-head">
              <h2>Employees {!employees.loading && `(${total})`}</h2>
            </div>

            <FilterChips chips={chips} />

            {employees.error && <div className="alert alert-error">{employees.error}</div>}

            <DataTable
              columns={columns}
              rows={items}
              loading={employees.loading}
              onRowClick={(employee) => navigate(paths.employee(employee.id))}
              empty={
                filtering ? (
                  <EmptyState
                    message="No employees match your filters."
                    action={
                      <button type="button" className="btn btn-sm" onClick={clear}>
                        Clear all filters
                      </button>
                    }
                  />
                ) : (
                  <EmptyState
                    message="No employees yet."
                    action={
                      <Link className="btn btn-sm" to={paths.newEmployee()}>
                        Add the first employee
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
      )}
    </>
  );
}

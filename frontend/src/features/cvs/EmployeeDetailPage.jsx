import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import DetailHeader from "../../components/DetailHeader";
import Tabs from "../../components/Tabs";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { getEmployee } from "../../api/employees";
import { dash, formatExperience } from "../../lib/format";
import EmployeeProfileTab from "./EmployeeProfileTab";
import EmployeeCvsTab from "./EmployeeCvsTab";
import EmployeeCertificationsTab from "./EmployeeCertificationsTab";
import CvDrawer from "./CvDrawer";
import CertificationDrawer from "./CertificationDrawer";
import { paths } from "../../app/routes";

const TABS = [
  { key: "profile", label: "Profile" },
  { key: "cvs", label: "CVs" },
  { key: "certifications", label: "Certifications" },
];

// The open drawer rides in the query string alongside the tab, so both are
// linkable and Back closes the drawer.
const VIEW_SCHEMA = { tab: "profile", cv: "", cert: "" };

export default function EmployeeDetailPage() {
  const { employeeId } = useParams();
  const navigate = useNavigate();
  const { values, setValues } = useQueryParams(VIEW_SCHEMA);

  const loadEmployee = useCallback(() => getEmployee(employeeId), [employeeId]);
  const employee = useResource(loadEmployee);

  const record = employee.data;
  const openCv = record && values.cv ? record.cvs.find((cv) => cv.id === values.cv) : null;
  const openCertification =
    record && values.cert
      ? record.certifications.find((certification) => certification.id === values.cert)
      : null;
  const cvDrawerOpen = values.cv === "new" || Boolean(openCv);
  const certDrawerOpen = values.cert === "new" || Boolean(openCertification);

  function closeDrawers() {
    setValues({ cv: "", cert: "" });
  }

  function handleSaved() {
    closeDrawers();
    employee.reload();
  }

  // Only while there is nothing to show yet. Every CV and certification save
  // reloads the employee, and without the data check that reload replaced
  // the whole page with "Loading..." each time.
  if (employee.loading && !employee.data) return <p className="muted">Loading...</p>;
  // A reload that fails once the record is on screen reports inline below
  // the header instead, as the project page does.
  if (employee.error && !employee.data) {
    return (
      <div className="card">
        <div className="alert alert-error">{employee.error}</div>
        <Link to={paths.cvs()}>Back to CVs</Link>
      </div>
    );
  }

  return (
    <>
      <p className="muted page-note">
        <Link to={paths.cvs()}>&larr; CVs</Link>
      </p>

      <DetailHeader
        title={record.full_name}
        subtitle={record.designation}
        facts={[
          { label: "Employee code", value: dash(record.employee_code) },
          { label: "Department", value: dash(record.department) },
          {
            label: "Total Professional Experience",
            value: formatExperience(record.total_experience_years),
          },
          {
            label: "Experience with VGIL",
            value: formatExperience(record.vgil_experience_years),
          },
          { label: "Email", value: dash(record.email) },
          { label: "Phone", value: dash(record.phone) },
        ]}
        actions={
          <>
            <button
              type="button"
              className="btn"
              onClick={() => navigate(paths.editEmployee(record.id))}
            >
              Edit
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setValues({ tab: "certifications", cv: "", cert: "new" })}
            >
              + Certification
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setValues({ tab: "cvs", cv: "new", cert: "" })}
            >
              + Upload CV
            </button>
          </>
        }
      />

      {employee.error && (
        <div className="alert alert-error">
          {employee.error} The employee below may be out of date — reload the page.
        </div>
      )}

      <Tabs
        tabs={TABS}
        active={values.tab}
        onChange={(tab) => setValues({ tab, cv: "", cert: "" })}
      />

      {values.tab === "cvs" && (
        <EmployeeCvsTab
          employee={record}
          onOpen={(cv) => setValues({ tab: "cvs", cv: cv.id, cert: "" })}
          onAdd={() => setValues({ tab: "cvs", cv: "new", cert: "" })}
        />
      )}
      {values.tab === "certifications" && (
        <EmployeeCertificationsTab
          employee={record}
          onOpen={(certification) =>
            setValues({ tab: "certifications", cv: "", cert: certification.id })
          }
          onAdd={() => setValues({ tab: "certifications", cv: "", cert: "new" })}
        />
      )}
      {values.tab !== "cvs" && values.tab !== "certifications" && (
        <EmployeeProfileTab employee={record} />
      )}

      {cvDrawerOpen && (
        <CvDrawer employee={record} cv={openCv} onClose={closeDrawers} onSaved={handleSaved} />
      )}
      {certDrawerOpen && (
        <CertificationDrawer
          employee={record}
          certification={openCertification}
          onClose={closeDrawers}
          onSaved={handleSaved}
        />
      )}
    </>
  );
}

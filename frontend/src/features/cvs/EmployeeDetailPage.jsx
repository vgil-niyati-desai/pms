import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import DetailHeader from "../../components/DetailHeader";
import DetailSkeleton from "../../components/DetailSkeleton";
import Tabs from "../../components/Tabs";
import useHandoffNotice from "../../hooks/useHandoffNotice";
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
// linkable and survive a refresh. Note that useQueryParams replaces rather
// than pushes, so Back leaves the employee rather than closing the drawer --
// Escape, the backdrop and Close do that, and NavigationGuard asks first when
// the drawer holds unsaved changes.
const VIEW_SCHEMA = { tab: "profile", cv: "", cert: "" };

export default function EmployeeDetailPage() {
  const { employeeId } = useParams();
  const navigate = useNavigate();
  const { values, setValues } = useQueryParams(VIEW_SCHEMA);
  // Handed over by the employee form after a save -- that happens on a screen
  // the user then leaves, so this is the only screen left to confirm it on.
  const [notice, setNotice] = useHandoffNotice();

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
  if (employee.loading && !employee.data) {
    return (
      <>
        <p className="muted page-note">
          <Link to={paths.cvs()}>&larr; CVs</Link>
        </p>
        <DetailSkeleton facts={6} tabs={TABS.length} />
      </>
    );
  }
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
        subtitleLabel="Designation"
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
              Edit employee
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setValues({ tab: "certifications", cv: "", cert: "new" })}
            >
              + Add certification
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

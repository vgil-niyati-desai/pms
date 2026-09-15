import { dash, formatExperience, formatTimestamp } from "../../lib/format";

export default function EmployeeProfileTab({ employee }) {
  const fields = [
    ["Employee code", dash(employee.employee_code)],
    ["Designation", dash(employee.designation)],
    ["Department", dash(employee.department)],
    // The two experience figures sit next to the dates they are worked out
    // from, so a number that looks wrong can be traced to the date behind it
    // without opening the edit form.
    ["Career start", dash(employee.career_start_date)],
    ["Total Professional Experience", formatExperience(employee.total_experience_years)],
    ["Date of joining", dash(employee.date_of_joining)],
    ["Experience with VGIL", formatExperience(employee.vgil_experience_years)],
    ["Highest qualification", dash(employee.highest_qualification)],
    ["Email", dash(employee.email)],
    ["Phone", dash(employee.phone)],
    ["Created", formatTimestamp(employee.created_at)],
    ["Last updated", formatTimestamp(employee.updated_at)],
  ];

  return (
    <>
      <section className="card">
        <h3>Details</h3>
        <dl className="detail-facts detail-facts-wide">
          {fields.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {(employee.key_skills || employee.notes) && (
        <section className="card">
          {employee.key_skills && (
            <>
              <h3>Key skills</h3>
              <p className="prose">{employee.key_skills}</p>
            </>
          )}
          {employee.notes && (
            <>
              <h3>Notes</h3>
              <p className="prose">{employee.notes}</p>
            </>
          )}
        </section>
      )}
    </>
  );
}

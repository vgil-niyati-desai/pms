import { dash, formatTimestamp } from "../../lib/format";

export default function EmployeeProfileTab({ employee }) {
  // Employee code, designation, department, the two experience figures, email
  // and phone are all in the header a few pixels above this tab. Repeating
  // them here filled the Profile with a second copy of the identity block --
  // the same thing the project Overview used to do -- and pushed the facts
  // that are NOT up there to the end of a list nobody read that far down.
  const fields = [
    // The two dates the header's experience figures are worked out from, so a
    // figure that looks wrong can be traced to the date behind it without
    // opening the edit form.
    ["Career start", dash(employee.career_start_date)],
    ["Date of joining", dash(employee.date_of_joining)],
    ["Highest qualification", dash(employee.highest_qualification)],
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

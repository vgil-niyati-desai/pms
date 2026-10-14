/** 'The tender “Metro bid” (cost receipt)' -- one record using a document. */
export function describeUse(use) {
  const roles = use.roles?.length ? ` (${use.roles.join(", ")})` : "";
  return `The ${use.kind} “${use.label}”${roles}`;
}

/** The records a document is also used by, as a confirmation dialog lists them. */
export default function DocumentUsageList({ uses }) {
  return (
    <ul className="muted">
      {uses.map((use) => (
        <li key={`${use.kind}-${use.id}`}>{describeUse(use)}</li>
      ))}
    </ul>
  );
}

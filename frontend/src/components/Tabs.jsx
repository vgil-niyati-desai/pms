/**
 * Tab strip whose selection lives in the URL, so a tab can be linked to and
 * survives a refresh. `tabs` is `[{ key, label }]`.
 */
export default function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={tab.key === active}
          className={tab.key === active ? "tab tab-active" : "tab"}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

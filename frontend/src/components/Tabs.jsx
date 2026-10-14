/**
 * Tab strip whose selection lives in the URL, so a tab can be linked to and
 * survives a refresh. `tabs` is `[{ key, label, count? }]`.
 *
 * `count` says how much the tab holds, so an empty one can be seen to be
 * empty without opening it. It is optional and only drawn for a number: a
 * tab whose contents have not loaded yet leaves it out rather than claiming
 * a zero it does not know.
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
          {/* The space is explicit: JSX drops the newline between them, and
              without it the tab reads as "Criteria7". */}
          {typeof tab.count === "number" && <> <span className="tab-count">{tab.count}</span></>}
        </button>
      ))}
    </div>
  );
}

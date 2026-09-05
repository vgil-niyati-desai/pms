import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Drawer from "../components/Drawer";
import { AREAS, sectionForPath } from "./routes";

function AreaNav({ onNavigate }) {
  return (
    <nav className="nav">
      {AREAS.map((area) => (
        <NavLink
          key={area.key}
          to={area.path}
          className={({ isActive }) => (isActive ? "nav-link nav-link-active" : "nav-link")}
          onClick={onNavigate}
        >
          {area.label}
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * Frame every screen renders inside: sidebar, topbar, and the routed content.
 *
 * The sidebar is permanent from 760px up. Below that it is hidden and the
 * same links are reached through the topbar's menu button, which opens them
 * in a left-hand drawer.
 */
export default function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [navOpen, setNavOpen] = useState(false);
  const section = sectionForPath(location.pathname);
  const action = section?.action;

  // A route change means the drawer has done its job.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Procurement MS</div>
        <AreaNav />
      </aside>

      <div className="shell-main">
        <header className="topbar">
          <button
            type="button"
            className="nav-toggle"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            aria-expanded={navOpen}
          >
            &#9776;
          </button>
          <h1 className="topbar-title">{section ? section.label : "Procurement MS"}</h1>
          {action && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => navigate(action.path)}
              disabled={!action.available}
              title={
                action.available
                  ? undefined
                  : `${action.label} becomes available when the ${section.label} module is built.`
              }
            >
              {action.label}
            </button>
          )}
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>

      {navOpen && (
        <Drawer title="Menu" side="left" onClose={() => setNavOpen(false)}>
          <AreaNav onNavigate={() => setNavOpen(false)} />
        </Drawer>
      )}
    </div>
  );
}

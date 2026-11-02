import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Drawer from "../components/Drawer";
import ErrorBoundary from "../components/ErrorBoundary";
import NavigationGuard from "./NavigationGuard";
import { UnsavedChangesProvider, useDirtyRegistry } from "./UnsavedChangesContext";
import { useAuth } from "./AuthContext";
import { AREAS, sectionForPath } from "./routes";

function AreaNav({ onNavigate, role }) {
  const areas = role === "Normal User"
    ? [{ key: "document-upload", label: "Upload document", path: "/documents/upload" }]
    : AREAS.filter((area) => !area.roles || area.roles.includes(role))
      .filter((area) => role === "Admin" || ["documents", "company-documents", "proposal-library"].includes(area.key));

  return (
    <nav className="nav">
      {areas.map((area) => (
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
function AppFrame() {
  const location = useLocation();
  const navigate = useNavigate();
  const dirtyRegistry = useDirtyRegistry();
  const { user, logout } = useAuth();
  const [navOpen, setNavOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const section = sectionForPath(location.pathname);
  const action = section?.action;

  // A route change means the drawer has done its job.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  async function handleLogout() {
    if (
      dirtyRegistry?.anyDirty() &&
      !window.confirm("Sign out now? Unsaved changes on this screen will be lost.")
    ) {
      return;
    }
    setLogoutError("");
    try {
      await logout();
      navigate("/login", { replace: true });
    } catch (error) {
      setLogoutError(error.message || "Could not sign out.");
    }
  }

  // The provider sits above everything a form can render in, and the guard
  // sits last so, at an equal z-index, it paints over the document preview.
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Procurement MS</div>
        <AreaNav role={user.role} />
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
          <div className="auth-controls">
            <span className="auth-user">{user.username}</span>
            {action && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => navigate(action.path)}
              >
                {action.label}
              </button>
            )}
            <button type="button" className="btn" onClick={handleLogout}>
              Sign out
            </button>
          </div>
        </header>

        <main className="content">
          {logoutError && <div className="alert alert-error" role="alert">{logoutError}</div>}
          {/* Keyed on the path so navigating away from a crashed page recovers. */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      {navOpen && (
        <Drawer title="Menu" side="left" onClose={() => setNavOpen(false)}>
          <AreaNav role={user.role} onNavigate={() => setNavOpen(false)} />
        </Drawer>
      )}

      <NavigationGuard />
    </div>
  );
}

export default function AppShell() {
  return (
    <UnsavedChangesProvider>
      <AppFrame />
    </UnsavedChangesProvider>
  );
}

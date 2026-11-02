import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

export default function RequireAuth() {
  const { user, loading, sessionError } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <main className="auth-page">
        <p className="muted" role="status">Checking your session…</p>
      </main>
    );
  }
  if (sessionError) {
    return (
      <main className="auth-page">
        <div className="card auth-card">
          <div className="alert alert-error" role="alert">{sessionError}</div>
          <button
            className="btn btn-primary"
            type="button"
            onClick={() => window.location.reload()}
          >
            Try again
          </button>
        </div>
      </main>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}

export function RequireRole({ roles }) {
  const { user } = useAuth();
  if (!roles.includes(user.role)) {
    return (
      <section className="card">
        <h2>Access denied</h2>
        <p>You do not have permission to open this page.</p>
      </section>
    );
  }
  return <Outlet />;
}

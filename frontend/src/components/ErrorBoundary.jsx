import { Component } from "react";
import { paths } from "../app/routes";

/**
 * Catches an error thrown while rendering anything beneath it and shows a
 * plain fallback instead of letting React unmount the whole tree to a blank
 * page. Only the message reaches the console -- never the screen.
 *
 * It uses a plain anchor for the way home, not a router Link, so the same
 * component also works as the outermost boundary, outside the router. A full
 * page load is the right reset after a crash anyway.
 *
 * Reset a boundary by giving it a new `key` (AppShell keys it on the path).
 */
export default class ErrorBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    console.error("Unexpected rendering error:", error, info?.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <ErrorFallback />;
  }
}

/**
 * What the boundary shows after a crash. Exported on its own because the
 * data router has a boundary of its own around the route tree: an error
 * thrown by the shell itself would be caught there, before the outermost
 * ErrorBoundary ever saw it, and shown as the router's stock error page. App
 * hands this to that route as its errorElement instead, so a crash looks the
 * same wherever it is caught.
 */
export function ErrorFallback() {
  return (
    <div className="card" role="alert">
      <h2>Something went wrong</h2>
      <p className="muted">
        This screen hit an unexpected problem and could not be shown. Your saved data is not
        affected. Reload the page to try again, or go back to the home page.
      </p>
      <p className="table-error">
        <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
          Reload
        </button>
        <a className="btn" href={paths.home()}>
          Go to home page
        </a>
      </p>
    </div>
  );
}

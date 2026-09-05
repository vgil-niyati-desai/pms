import { Link } from "react-router-dom";
import { paths } from "../app/routes";

export default function NotFoundPage() {
  return (
    <div className="card">
      <h2>Page not found</h2>
      <p className="muted">That address doesn&apos;t match any screen in this application.</p>
      <p>
        <Link to={paths.projects()}>Go to Projects</Link>
      </p>
    </div>
  );
}

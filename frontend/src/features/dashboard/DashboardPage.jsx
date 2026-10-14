import { useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import DataTable from "../../components/DataTable";
import EmptyState from "../../components/EmptyState";
import Skeleton from "../../components/Skeleton";
import StatusPill from "../../components/StatusPill";
import FileViewLink from "../../components/FileViewLink";
import useResource from "../../hooks/useResource";
import { listProjects } from "../../api/projects";
import { listEmployees, listCertifications } from "../../api/employees";
import { listDocuments } from "../../api/documents";
import { settleStats, statsErrors } from "../../lib/dashboardStats";
import { dash, formatTimestamp } from "../../lib/format";
import { paths } from "../../app/routes";

const RECENT_PAGE_SIZE = 5;

/**
 * The home page: four counts worth knowing at a glance, and the two lists
 * most worth checking first. Built entirely from endpoints the Projects,
 * CVs and Documents screens already call — a page size of 1 against a
 * filtered list is enough to read its `total`, so a count here costs the
 * same request those screens already make for the first page of a filter.
 *
 * Deliberately not a KPI dashboard: there is no won/lost tracking, no
 * normalised financial figures, and no user accounts behind submitted_by,
 * so none of those are shown as a number here — see the four tiles below for
 * exactly what the data already settles.
 */
export default function DashboardPage() {
  const navigate = useNavigate();

  const loadStats = useCallback(
    () =>
      settleStats({
        ongoing: () => listProjects({ status: "Ongoing", page: 1, pageSize: 1 }),
        completed: () => listProjects({ status: "Completed", page: 1, pageSize: 1 }),
        employees: () => listEmployees({ page: 1, pageSize: 1 }),
        certificationsExpired: () =>
          listCertifications({ status: "Expired", page: 1, pageSize: 1 }),
      }),
    [],
  );
  const stats = useResource(loadStats);

  const loadRecentProjects = useCallback(
    () => listProjects({ sort: "-updated_at", page: 1, pageSize: RECENT_PAGE_SIZE }),
    [],
  );
  const recentProjects = useResource(loadRecentProjects, { initialData: { items: [], total: 0 } });

  // Documents have no updated_at of their own -- only created_at -- so
  // "recently added" is the honest reading of this list, not "recently
  // updated". Newest-first by created_at is also this list's own default
  // sort, the same one the Documents screen opens on.
  const loadRecentDocuments = useCallback(
    () => listDocuments({ sort: "-created_at", page: 1, pageSize: RECENT_PAGE_SIZE }),
    [],
  );
  const recentDocuments = useResource(loadRecentDocuments, { initialData: { items: [], total: 0 } });

  const projectColumns = [
    { key: "title", header: "Project title", className: "cell-name" },
    { key: "client_name", header: "Client", render: (project) => dash(project.client_name) },
    {
      key: "status",
      header: "Status",
      className: "cell-tight",
      render: (project) => <StatusPill value={project.status} />,
    },
    {
      key: "updated_at",
      header: "Updated",
      className: "cell-tight",
      render: (project) => formatTimestamp(project.updated_at),
    },
  ];

  const documentColumns = [
    {
      key: "document_type",
      header: "Type",
      render: (document) => <StatusPill value={document.document_type} />,
    },
    { key: "client_name", header: "Client", render: (document) => dash(document.client_name) },
    {
      key: "file",
      header: "File",
      className: "cell-tight",
      render: (document) => (
        <FileViewLink
          documentId={document.file_name ? document.id : null}
          fileName={document.file_name}
          title={`${document.document_type || "Document"} — ${document.client_name || ""}`}
        />
      ),
    },
    {
      key: "created_at",
      header: "Added",
      className: "cell-tight",
      render: (document) => formatTimestamp(document.created_at),
    },
  ];

  // Each tile drills into the list that already answers it -- the same
  // filter a user would set by hand, just arrived at from the count instead.
  const statTiles = [
    {
      key: "ongoing",
      label: "Ongoing Projects",
      value: stats.data?.ongoing?.value,
      error: stats.data?.ongoing?.error,
      to: `${paths.projects()}?status=Ongoing`,
    },
    {
      key: "completed",
      label: "Completed Projects",
      value: stats.data?.completed?.value,
      error: stats.data?.completed?.error,
      to: `${paths.projects()}?status=Completed`,
    },
    {
      key: "employees",
      label: "Total Employees",
      value: stats.data?.employees?.value,
      error: stats.data?.employees?.error,
      to: paths.cvs(),
    },
    {
      key: "expired-certifications",
      label: "Expired Certifications",
      value: stats.data?.certificationsExpired?.value,
      error: stats.data?.certificationsExpired?.error,
      to: `${paths.cvs()}?view=certifications&certStatus=Expired`,
      warn: true,
    },
  ];

  const failedStats = statsErrors(stats.data);

  return (
    <div className="list-layout">
      {(stats.error || failedStats.length > 0) && (
        <div className="alert alert-error table-error" role="alert">
          <span>
            {stats.error ||
              `Some summary figures could not be loaded: ${failedStats.join("; ")}`}
          </span>
          <button type="button" className="btn btn-sm" onClick={stats.reload}>
            Retry
          </button>
        </div>
      )}

      <section className="stat-grid">
        {statTiles.map((tile) => (
          <Link
            key={tile.key}
            to={tile.to}
            className={
              tile.warn && tile.value > 0 ? "card stat-tile stat-tile-warn" : "card stat-tile"
            }
          >
            <span className="stat-label">{tile.label}</span>
            {stats.loading ? (
              <Skeleton width="48px" height={28} />
            ) : (
              <span className="stat-value">
                {tile.value ?? "—"}
                {tile.error && (
                  <span className="stat-error" title={tile.error} aria-label="Failed to load">
                    {" "}
                    !
                  </span>
                )}
              </span>
            )}
          </Link>
        ))}
      </section>

      <section className="card">
        <div className="list-head">
          <h2>Recently updated projects</h2>
          <Link className="btn btn-sm" to={paths.projects()}>
            View all projects
          </Link>
        </div>

        <DataTable
          columns={projectColumns}
          rows={recentProjects.data?.items ?? []}
          loading={recentProjects.loading}
          error={recentProjects.error}
          onRetry={recentProjects.reload}
          onRowClick={(project) => navigate(paths.project(project.id))}
          empty={
            <EmptyState
              message="No projects yet."
              action={
                <Link className="btn btn-sm" to={paths.newProject()}>
                  Create the first project
                </Link>
              }
            />
          }
        />
      </section>

      <section className="card">
        <div className="list-head">
          <h2>Recently added documents</h2>
          <Link className="btn btn-sm" to={paths.documents()}>
            View all documents
          </Link>
        </div>

        <DataTable
          columns={documentColumns}
          rows={recentDocuments.data?.items ?? []}
          loading={recentDocuments.loading}
          error={recentDocuments.error}
          onRetry={recentDocuments.reload}
          empty={<EmptyState message="No documents yet." />}
        />
      </section>
    </div>
  );
}

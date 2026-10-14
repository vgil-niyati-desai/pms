import { useEffect } from "react";
import {
  Route,
  RouterProvider,
  createBrowserRouter,
  createRoutesFromElements,
  useRouteError,
} from "react-router-dom";
import AppShell from "./app/AppShell";
import { paths } from "./app/routes";
import { ErrorFallback } from "./components/ErrorBoundary";
import DashboardPage from "./features/dashboard/DashboardPage";
import ProjectsListPage from "./features/projects/ProjectsListPage";
import ProjectFormPage from "./features/projects/ProjectFormPage";
import ProjectDetailPage from "./features/projects/ProjectDetailPage";
import EmployeesListPage from "./features/cvs/EmployeesListPage";
import EmployeeFormPage from "./features/cvs/EmployeeFormPage";
import EmployeeDetailPage from "./features/cvs/EmployeeDetailPage";
import TendersListPage from "./features/tenders/TendersListPage";
import TenderFormPage from "./features/tenders/TenderFormPage";
import TenderDetailPage from "./features/tenders/TenderDetailPage";
import DocumentsPage from "./features/documents/DocumentsPage";
import NotFoundPage from "./features/NotFoundPage";

/**
 * Every screen renders inside AppShell, so the sidebar and topbar are
 * mounted once and survive navigation.
 *
 * Each form page is mounted by both its "new" and its "edit" route. The keys
 * tell React they are different screens: without them, "+ New" pressed on an
 * edit screen reused that mounted form, still holding the record's values.
 *
 * A data router rather than <BrowserRouter> with <Routes>, because only a
 * data router can stop a navigation: useBlocker, which NavigationGuard uses
 * to ask about unsaved changes before the sidebar, the topbar or the browser's
 * Back leaves a form, throws under anything else. The route tree is the same
 * one as before, built once at module load.
 *
 * A data router also catches render errors itself, ahead of the ErrorBoundary
 * in main.jsx. The errorElement keeps those looking as they did: the same
 * fallback, and the error logged the same way. Errors inside a screen are
 * still caught first by the boundary AppShell keeps around the Outlet.
 */
function RouteError() {
  const error = useRouteError();
  useEffect(() => {
    console.error("Unexpected rendering error:", error);
  }, [error]);
  return <ErrorFallback />;
}

const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<AppShell />} errorElement={<RouteError />}>
      <Route path={paths.home()} element={<DashboardPage />} />
      <Route path={paths.projects()} element={<ProjectsListPage />} />
      <Route path={paths.newProject()} element={<ProjectFormPage key="new" />} />
      <Route path={paths.project(":projectId")} element={<ProjectDetailPage />} />
      <Route path={paths.editProject(":projectId")} element={<ProjectFormPage key="edit" />} />
      <Route path={paths.cvs()} element={<EmployeesListPage />} />
      <Route path={paths.newEmployee()} element={<EmployeeFormPage key="new" />} />
      <Route path={paths.employee(":employeeId")} element={<EmployeeDetailPage />} />
      <Route path={paths.editEmployee(":employeeId")} element={<EmployeeFormPage key="edit" />} />
      <Route path={paths.tenders()} element={<TendersListPage />} />
      <Route path={paths.newTender()} element={<TenderFormPage key="new" />} />
      <Route path={paths.tender(":tenderId")} element={<TenderDetailPage />} />
      <Route path={paths.editTender(":tenderId")} element={<TenderFormPage key="edit" />} />
      <Route path={paths.documents()} element={<DocumentsPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>,
  ),
);

export default function App() {
  return <RouterProvider router={router} />;
}

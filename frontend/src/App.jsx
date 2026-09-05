import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./app/AppShell";
import { paths } from "./app/routes";
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
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path={paths.home()} element={<Navigate to={paths.projects()} replace />} />
        <Route path={paths.projects()} element={<ProjectsListPage />} />
        <Route path={paths.newProject()} element={<ProjectFormPage />} />
        <Route path={paths.project(":projectId")} element={<ProjectDetailPage />} />
        <Route path={paths.editProject(":projectId")} element={<ProjectFormPage />} />
        <Route path={paths.cvs()} element={<EmployeesListPage />} />
        <Route path={paths.newEmployee()} element={<EmployeeFormPage />} />
        <Route path={paths.employee(":employeeId")} element={<EmployeeDetailPage />} />
        <Route path={paths.editEmployee(":employeeId")} element={<EmployeeFormPage />} />
        <Route path={paths.tenders()} element={<TendersListPage />} />
        <Route path={paths.newTender()} element={<TenderFormPage />} />
        <Route path={paths.tender(":tenderId")} element={<TenderDetailPage />} />
        <Route path={paths.editTender(":tenderId")} element={<TenderFormPage />} />
        <Route path={paths.documents()} element={<DocumentsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

import { useCallback, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import DetailHeader from "../../components/DetailHeader";
import StatusPill from "../../components/StatusPill";
import TagChip from "../../components/TagChip";
import Tabs from "../../components/Tabs";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { listDocuments } from "../../api/documents";
import { getProject, linkDocument, unlinkDocument } from "../../api/projects";
import { dash, formatPeriod, EVIDENCE_TYPES, DOCUMENT_TYPES } from "./projectFields";
import ProjectOverviewTab from "./ProjectOverviewTab";
import AttachedDocumentsTab from "../documents/AttachedDocumentsTab";
import AttachedDocumentDrawer from "../documents/AttachedDocumentDrawer";
import { paths } from "../../app/routes";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "documents", label: "Documents" },
];

// The drawer's state rides in the query string alongside the tab, so a tab
// and an open document are both linkable and Back closes the drawer.
const VIEW_SCHEMA = { tab: "overview", doc: "", type: "" };

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { values, setValues } = useQueryParams(VIEW_SCHEMA);

  const loadProject = useCallback(() => getProject(projectId), [projectId]);
  const project = useResource(loadProject);

  const loadDocuments = useCallback(() => listDocuments(), []);
  const documents = useResource(loadDocuments, { initialData: [] });

  // Documents belong to a project through the project's own id list, until
  // the backend carries a project_id on the document itself.
  const projectDocuments = useMemo(() => {
    if (!project.data) return [];
    const ids = new Set(project.data.document_ids);
    return documents.data.filter((document) => ids.has(document.id));
  }, [project.data, documents.data]);

  const heldTypes = useMemo(
    () => [...new Set(projectDocuments.map((document) => document.document_type))],
    [projectDocuments],
  );

  const openDocument = values.doc
    ? projectDocuments.find((document) => document.id === values.doc)
    : null;
  const drawerOpen = values.doc === "new" || Boolean(openDocument);

  function openDrawer(document) {
    setValues({ tab: "documents", doc: document.id, type: "" });
  }

  function openAddDrawer(presetType = "") {
    setValues({ tab: "documents", doc: "new", type: presetType });
  }

  function closeDrawer() {
    setValues({ doc: "", type: "" });
  }

  function handleSaved() {
    closeDrawer();
    project.reload();
    documents.reload();
  }

  if (project.loading) return <p className="muted">Loading...</p>;
  if (project.error) {
    return (
      <div className="card">
        <div className="alert alert-error">{project.error}</div>
        <Link to={paths.projects()}>Back to Projects</Link>
      </div>
    );
  }

  const record = project.data;

  return (
    <>
      <p className="muted page-note">
        <Link to={paths.projects()}>&larr; Projects</Link>
      </p>

      <DetailHeader
        title={record.title}
        subtitle={record.client_name}
        facts={[
          { label: "Reference no.", value: dash(record.reference_number) },
          { label: "Contract value", value: dash(record.contract_value) },
          { label: "Period", value: formatPeriod(record.start_date, record.end_date) },
          { label: "Department", value: dash(record.department) },
        ]}
        badges={
          <>
            {record.status && <StatusPill value={record.status} />}
            {record.tags.map((tag) => (
              <TagChip key={tag} value={tag} />
            ))}
          </>
        }
        actions={
          <>
            <button
              type="button"
              className="btn"
              onClick={() => navigate(paths.editProject(record.id))}
            >
              Edit
            </button>
            <button type="button" className="btn btn-primary" onClick={() => openAddDrawer()}>
              + Add document
            </button>
          </>
        }
      />

      <Tabs
        tabs={TABS}
        active={values.tab}
        onChange={(tab) => setValues({ tab, doc: "", type: "" })}
      />

      {values.tab === "documents" ? (
        <AttachedDocumentsTab
          documents={projectDocuments}
          typeOrder={EVIDENCE_TYPES}
          loading={documents.loading}
          error={documents.error}
          onOpen={openDrawer}
          onAdd={openAddDrawer}
          emptyMessage="No documents attached to this project yet."
        />
      ) : (
        <ProjectOverviewTab
          project={record}
          heldTypes={heldTypes}
          onAddMissing={(type) => openAddDrawer(type)}
        />
      )}

      {drawerOpen && (
        <AttachedDocumentDrawer
          context={{
            subjectName: record.client_name,
            title: record.title,
            contractValue: record.contract_value,
            department: record.department,
          }}
          documentTypes={DOCUMENT_TYPES}
          document={openDocument}
          presetType={values.type}
          onLink={(documentId) => linkDocument(record.id, documentId)}
          onUnlink={(documentId) => unlinkDocument(record.id, documentId)}
          onClose={closeDrawer}
          onSaved={handleSaved}
        />
      )}
    </>
  );
}

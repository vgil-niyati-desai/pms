import { useCallback, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import DetailHeader from "../../components/DetailHeader";
import StatusPill from "../../components/StatusPill";
import TagChip from "../../components/TagChip";
import Tabs from "../../components/Tabs";
import useHandoffNotice from "../../hooks/useHandoffNotice";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import {
  getProject,
  linkDocument,
  listProjectDocuments,
  unlinkDocument,
} from "../../api/projects";
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
// and an open document are both linkable and survive a refresh. Note that
// useQueryParams replaces rather than pushes, so Back leaves the project
// rather than closing the drawer -- Escape, the backdrop and Close do that.
//
//   tab   which tab is open
//   doc   the open document, or "new" for the add form
//   type  the type that add form starts on, from a missing evidence pill
//   group the Documents tab's type filter, from a held evidence pill
const VIEW_SCHEMA = { tab: "overview", doc: "", type: "", group: "" };

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { values, setValues } = useQueryParams(VIEW_SCHEMA);
  // Set here after a document action, and handed over by the project form
  // after a save -- that one happens on a screen the user then leaves.
  const [notice, setNotice] = useHandoffNotice();

  const loadProject = useCallback(() => getProject(projectId), [projectId]);
  const project = useResource(loadProject);

  // This project's documents, from the endpoint that knows which they are.
  // It used to be the entire document log, filtered here against
  // project.document_ids, because the backend could not answer it.
  const loadDocuments = useCallback(() => listProjectDocuments(projectId), [projectId]);
  const documents = useResource(loadDocuments, { initialData: [] });
  const projectDocuments = documents.data;

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
    // The group filter is dropped on the way in: adding a Contract while
    // the tab is narrowed to LOI would otherwise file the new document
    // into a group the filter is hiding, and the add would look like it
    // failed.
    setValues({ tab: "documents", doc: "new", type: presetType, group: "" });
  }

  // A held evidence pill: show the documents that provide it, through the
  // tab's own type filter rather than a second viewer of its own.
  function openTypeGroup(type) {
    setValues({ tab: "documents", group: type, doc: "", type: "" });
  }

  function closeDrawer() {
    setValues({ doc: "", type: "" });
  }

  function handleSaved(outcome = {}) {
    const { message = null, reveal = false } = outcome;
    // A document that has just appeared must not be filed into a group the
    // type filter is hiding: the save would look like it failed. Editing
    // and deleting leave the filter alone, because nothing new appeared
    // and losing the filter would lose the user's place.
    setValues(reveal ? { doc: "", type: "", group: "" } : { doc: "", type: "" });
    setNotice(message);
    project.reload();
    documents.reload();
  }

  // Only while there is nothing to show yet. handleSaved reloads the project
  // after every document add, edit and delete, and without the data check
  // that reload replaced the header, the tabs and the open tab's contents
  // with "Loading..." each time the drawer saved.
  if (project.loading && !project.data) return <p className="muted">Loading...</p>;
  // Only when there is nothing to fall back on. A reload that fails after
  // the record is already on screen -- the one handleSaved fires after every
  // document action -- used to replace the whole page with this card, so a
  // save that worked read as a page that broke. It reports inline instead,
  // below the header, and the record stays where it was.
  if (project.error && !project.data) {
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
        subtitleLabel="Client"
        subtitle={record.client_name}
        facts={[
          { label: "Reference no.", value: dash(record.reference_number) },
          { label: "Contract value", value: dash(record.contract_value) },
          { label: "Duration", value: formatPeriod(record.start_date, record.end_date) },
          { label: "Department", value: dash(record.department) },
        ]}
        // The status is part of what this record *is*, so it reads beside
        // the title. The tags are how it is found again, and stay below.
        titleBadges={record.status && <StatusPill value={record.status} />}
        badges={
          record.tags.length ? (
            <>
              {record.tags.map((tag) => (
                <TagChip key={tag} value={tag} />
              ))}
            </>
          ) : null
        }
        actions={
          <>
            <button
              type="button"
              className="btn"
              onClick={() => navigate(paths.editProject(record.id))}
            >
              Edit project
            </button>
            <button type="button" className="btn btn-primary" onClick={() => openAddDrawer()}>
              + Add document
            </button>
          </>
        }
      />

      {notice && (
        <div className="alert alert-success notice-bar" role="status">
          <span>{notice}</span>
          <button
            type="button"
            className="notice-dismiss"
            onClick={() => setNotice(null)}
            aria-label="Dismiss this message"
          >
            &times;
          </button>
        </div>
      )}

      {project.error && (
        <div className="alert alert-error">
          {project.error} The project below may be out of date — reload the page.
        </div>
      )}

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
          typeFilter={values.group}
          onTypeFilterChange={(group) => setValues({ group })}
          emptyMessage="No documents attached to this project yet."
        />
      ) : (
        <ProjectOverviewTab
          project={record}
          heldTypes={heldTypes}
          documentsLoading={documents.loading}
          documentsError={documents.error}
          onAddMissing={(type) => openAddDrawer(type)}
          onShowDocuments={openTypeGroup}
        />
      )}

      {drawerOpen && (
        <AttachedDocumentDrawer
          context={{
            subjectName: record.client_name,
            title: record.title,
            contractValue: record.contract_value,
            department: record.department,
            // Sent with the upload, so one request both logs the document
            // and attaches it. The explicit link below still runs and is a
            // no-op; between them the attachment survives either failing.
            projectId: record.id,
          }}
          documentTypes={DOCUMENT_TYPES}
          document={openDocument}
          presetType={values.type}
          // So the picker can mark what this project already holds
          // rather than offering to attach it a second time.
          attachedIds={projectDocuments.map((d) => d.id)}
          onLink={(documentId) => linkDocument(record.id, documentId)}
          onUnlink={(documentId) => unlinkDocument(record.id, documentId)}
          onClose={closeDrawer}
          onSaved={handleSaved}
        />
      )}
    </>
  );
}

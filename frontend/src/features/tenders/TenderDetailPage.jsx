import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import DetailHeader from "../../components/DetailHeader";
import DetailSkeleton from "../../components/DetailSkeleton";
import StatusPill from "../../components/StatusPill";
import TagChip from "../../components/TagChip";
import Tabs from "../../components/Tabs";
import useHandoffNotice from "../../hooks/useHandoffNotice";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import {
  OPEN_STATUSES,
  citeProject,
  getTender,
  linkDocument,
  listTenderDocuments,
  listTenderProjects,
  unciteProject,
  unlinkDocument,
} from "../../api/tenders";
import { dash, formatAmount } from "../../lib/format";
import AttachedDocumentsTab from "../documents/AttachedDocumentsTab";
import AttachedDocumentDrawer from "../documents/AttachedDocumentDrawer";
import { documentTypeOptions } from "../documents/documentTypes";
import { listDocumentTypes } from "../../api/documents";
import TenderOverviewTab from "./TenderOverviewTab";
import TenderCostsTab from "./TenderCostsTab";
import TenderCostDrawer from "./TenderCostDrawer";
import TenderCertificateDrawer from "./TenderCertificateDrawer";
import TenderEvidenceTab from "./TenderEvidenceTab";
import CiteProjectDrawer from "./CiteProjectDrawer";
import TenderCriteriaTab from "./TenderCriteriaTab";
import CriterionDrawer from "./CriterionDrawer";
import CandidatesDrawer from "./CandidatesDrawer";
import { TENDER_DOCUMENT_TYPES, deadlineUrgency } from "./tenderFields";
import { paths } from "../../app/routes";

// Criteria are what the tender asks for; the evidence tab is the past work
// this bid cites, which is a different thing from its own documents.
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "criteria", label: "Criteria" },
  { key: "costs", label: "Costs & certificates" },
  { key: "documents", label: "Documents" },
  // The key stays "evidence" so existing ?tab=evidence links keep working.
  { key: "evidence", label: "Cited projects" },
];

// Whichever drawer is open rides in the query string alongside the tab, so
// both are linkable and survive a refresh. Note that useQueryParams replaces
// rather than pushes, so Back leaves the tender rather than closing the
// drawer -- Escape, the backdrop and Close do that, and NavigationGuard asks
// first when the drawer holds unsaved changes.
const VIEW_SCHEMA = {
  tab: "overview",
  cost: "",
  cert: "",
  doc: "",
  type: "",
  cite: "",
  criterion: "",
  // The criterion whose candidates are being looked at.
  candidates: "",
};

export default function TenderDetailPage() {
  const { tenderId } = useParams();
  const navigate = useNavigate();
  const { values, setValues } = useQueryParams(VIEW_SCHEMA);
  // Handed over by the tender form after a save -- that happens on a screen
  // the user then leaves, so this is the only screen left to confirm it on.
  const [notice, setNotice] = useHandoffNotice();
  // Which citation is being removed, so its own button can say so.
  const [removingProjectId, setRemovingProjectId] = useState(null);

  const loadTender = useCallback(() => getTender(tenderId), [tenderId]);
  const tender = useResource(loadTender);

  // Resolved by the server, which knows which documents are this tender's.
  const loadDocuments = useCallback(() => listTenderDocuments(tenderId), [tenderId]);
  const documents = useResource(loadDocuments, { initialData: [] });

  // The past projects this bid cites. A separate list from its documents,
  // and deliberately so: citing a project does not attach its documents.
  const loadProjects = useCallback(() => listTenderProjects(tenderId), [tenderId]);
  const citedProjects = useResource(loadProjects, { initialData: [] });

  // What a document uploaded here can be typed as: the tender's own types
  // first, then every other type the log knows -- the Documents page's list
  // -- so a supporting document can carry the type a criterion asks for.
  const loadDocumentTypes = useCallback(() => listDocumentTypes(), []);
  const storedDocumentTypes = useResource(loadDocumentTypes, { initialData: [] });
  const uploadDocumentTypes = [
    ...TENDER_DOCUMENT_TYPES,
    ...documentTypeOptions(storedDocumentTypes.data).filter(
      (type) => !TENDER_DOCUMENT_TYPES.includes(type),
    ),
  ];

  const record = tender.data;
  const tenderDocuments = documents.data;

  const openCost = record && values.cost ? record.cost_items.find((c) => c.id === values.cost) : null;
  const openCertificate =
    record && values.cert ? record.certificates.find((c) => c.id === values.cert) : null;
  const openDocument = values.doc
    ? tenderDocuments.find((document) => document.id === values.doc)
    : null;

  const openCriterion =
    record && values.criterion
      ? (record.criteria || []).find((criterion) => criterion.id === values.criterion)
      : null;

  const costDrawerOpen = values.cost === "new" || Boolean(openCost);
  const certDrawerOpen = values.cert === "new" || Boolean(openCertificate);
  const docDrawerOpen = values.doc === "new" || Boolean(openDocument);
  const criterionDrawerOpen = values.criterion === "new" || Boolean(openCriterion);
  const candidatesCriterion =
    record && values.candidates
      ? (record.criteria || []).find((criterion) => criterion.id === values.candidates)
      : null;

  function closeDrawers() {
    setValues({ cost: "", cert: "", doc: "", type: "", cite: "", criterion: "", candidates: "" });
  }

  // Citing from the Evidence tab's drawer and from a criterion's candidates
  // is the same act, so both go through here.
  async function citeAndReload(projectId) {
    await citeProject(record.id, projectId);
    await citedProjects.reload();
    tender.reload();
  }

  // Linking a document a candidate search found: the same link the
  // Documents tab's "Attach existing" makes. The document is not copied.
  async function linkAndReload(documentId) {
    await linkDocument(record.id, documentId);
    documents.reload();
    tender.reload();
  }

  async function handleUncite(project) {
    setRemovingProjectId(project.id);
    try {
      await unciteProject(record.id, project.id);
      await citedProjects.reload();
      tender.reload();
    } finally {
      setRemovingProjectId(null);
    }
  }

  function handleSaved() {
    closeDrawers();
    tender.reload();
    documents.reload();
  }

  // Document saves alone report a message -- costs, certificates and
  // criteria go through handleSaved above and stay as quiet as they always
  // were, which is outside what this fix touches.
  function handleDocumentSaved(outcome = {}) {
    const { message = null } = outcome;
    closeDrawers();
    tender.reload();
    documents.reload();
    setNotice(message);
  }

  // Only while there is nothing to show yet. Every cite, uncite and drawer
  // save reloads the tender, and without the data check that reload replaced
  // the whole page with "Loading..." and unmounted whichever drawer was open.
  if (tender.loading && !tender.data) {
    return (
      <>
        <p className="muted page-note">
          <Link to={paths.tenders()}>&larr; Tenders</Link>
        </p>
        <DetailSkeleton facts={4} tabs={TABS.length} />
      </>
    );
  }
  // A reload that fails once the record is on screen reports inline below
  // the header instead, as the project page does.
  if (tender.error && !tender.data) {
    return (
      <div className="card">
        <div className="alert alert-error">{tender.error}</div>
        <Link to={paths.tenders()}>Back to Tenders</Link>
      </div>
    );
  }

  // How close the deadline is, on exactly the terms the list uses it: only
  // while the bid is still open, since a passed deadline on a tender already
  // won or withdrawn is not news.
  const urgency =
    OPEN_STATUSES.includes(record.status) && deadlineUrgency(record.submission_deadline);

  // Counts from what each tab is already given, so a tab can be seen to be
  // empty without opening it. The tender's own arrays are on the record that
  // got us past the guards above; the two separately loaded lists report
  // undefined until they have something, rather than a zero they cannot
  // vouch for. A reload after a save keeps the previous count, the way the
  // tabs themselves keep their rows.
  const counts = {
    criteria: (record.criteria || []).length,
    costs: record.cost_items.length + record.certificates.length,
    documents:
      documents.error || (documents.loading && tenderDocuments.length === 0)
        ? undefined
        : tenderDocuments.length,
    evidence:
      citedProjects.error || (citedProjects.loading && citedProjects.data.length === 0)
        ? undefined
        : citedProjects.data.length,
  };
  const tabs = TABS.map((tab) => ({ ...tab, count: counts[tab.key] }));

  return (
    <>
      <p className="muted page-note">
        <Link to={paths.tenders()}>&larr; Tenders</Link>
      </p>

      <DetailHeader
        title={record.title}
        subtitleLabel="Issuing authority"
        subtitle={record.issuing_authority}
        facts={[
          { label: "Reference no.", value: dash(record.reference_number) },
          { label: "Estimated value", value: formatAmount(record.estimated_value) },
          {
            label: "Submission deadline",
            // The date stays; the flag is the same one the list draws beside it.
            value: (
              <>
                {dash(record.submission_deadline)}
                {urgency && <span className="deadline-flag">{urgency}</span>}
              </>
            ),
          },
          { label: "EMD required", value: formatAmount(record.emd_amount) },
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
              onClick={() => navigate(paths.editTender(record.id))}
            >
              Edit tender
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setValues({ tab: "costs", cost: "new", cert: "", doc: "", type: "" })}
            >
              + Add cost
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() =>
                setValues({ tab: "documents", cost: "", cert: "", doc: "new", type: "" })
              }
            >
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

      {tender.error && (
        <div className="alert alert-error">
          {tender.error} The tender below may be out of date — reload the page.
        </div>
      )}

      <Tabs
        tabs={tabs}
        active={values.tab}
        onChange={(tab) =>
          setValues({ tab, cost: "", cert: "", doc: "", type: "", criterion: "", candidates: "" })
        }
      />

      {values.tab === "criteria" && (
        <TenderCriteriaTab
          tender={record}
          onOpen={(criterion) => setValues({ tab: "criteria", criterion: criterion.id })}
          onAdd={() => setValues({ tab: "criteria", criterion: "new" })}
          onChanged={() => tender.reload()}
          onFindCandidates={(criterion) => setValues({ tab: "criteria", candidates: criterion.id })}
        />
      )}

      {values.tab === "costs" && (
        <TenderCostsTab
          tender={record}
          onOpenCost={(item) => setValues({ tab: "costs", cost: item.id, cert: "", doc: "" })}
          onAddCost={() => setValues({ tab: "costs", cost: "new", cert: "", doc: "" })}
          onOpenCertificate={(certificate) =>
            setValues({ tab: "costs", cost: "", cert: certificate.id, doc: "" })
          }
          onAddCertificate={() => setValues({ tab: "costs", cost: "", cert: "new", doc: "" })}
        />
      )}

      {values.tab === "documents" && (
        <AttachedDocumentsTab
          documents={tenderDocuments}
          typeOrder={TENDER_DOCUMENT_TYPES}
          loading={documents.loading}
          error={documents.error}
          onOpen={(document) =>
            setValues({ tab: "documents", cost: "", cert: "", doc: document.id, type: "" })
          }
          onAdd={(presetType = "") =>
            setValues({ tab: "documents", cost: "", cert: "", doc: "new", type: presetType })
          }
          emptyMessage="No documents attached to this tender yet."
        />
      )}

      {values.tab === "evidence" && (
        <TenderEvidenceTab
          projects={citedProjects.data}
          loading={citedProjects.loading}
          error={citedProjects.error}
          removingId={removingProjectId}
          onOpen={(project) => navigate(paths.project(project.id))}
          onCite={() => setValues({ tab: "evidence", cite: "new" })}
          onRemove={handleUncite}
        />
      )}

      {values.tab !== "costs" &&
        values.tab !== "documents" &&
        values.tab !== "evidence" &&
        values.tab !== "criteria" && (
          <TenderOverviewTab
            tender={record}
            documentCount={tenderDocuments.length}
            citedProjectCount={citedProjects.data.length}
          />
        )}

      {costDrawerOpen && (
        <TenderCostDrawer
          tender={record}
          costItem={openCost}
          onClose={closeDrawers}
          onSaved={handleSaved}
        />
      )}
      {certDrawerOpen && (
        <TenderCertificateDrawer
          tender={record}
          certificate={openCertificate}
          onClose={closeDrawers}
          onSaved={handleSaved}
        />
      )}
      {criterionDrawerOpen && (
        <CriterionDrawer
          tender={record}
          criterion={openCriterion}
          onClose={closeDrawers}
          onSaved={handleSaved}
        />
      )}
      {candidatesCriterion && (
        <CandidatesDrawer
          tender={record}
          criterion={candidatesCriterion}
          onCite={citeAndReload}
          onLinkDocument={linkAndReload}
          onClose={closeDrawers}
        />
      )}
      {values.cite === "new" && (
        <CiteProjectDrawer
          citedIds={record.cited_project_ids}
          onCite={citeAndReload}
          onClose={closeDrawers}
        />
      )}
      {docDrawerOpen && (
        <AttachedDocumentDrawer
          context={{
            tenderId: record.id,
            subjectName: record.issuing_authority,
            title: record.title,
            contractValue: record.estimated_value,
            department: "",
          }}
          documentTypes={uploadDocumentTypes}
          attachedIds={tenderDocuments.map((d) => d.id)}
          document={openDocument}
          presetType={values.type}
          onLink={(documentId) => linkDocument(record.id, documentId)}
          onUnlink={(documentId) => unlinkDocument(record.id, documentId)}
          onClose={closeDrawers}
          onSaved={handleDocumentSaved}
        />
      )}
    </>
  );
}

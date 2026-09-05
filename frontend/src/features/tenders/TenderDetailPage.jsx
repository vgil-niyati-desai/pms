import { useCallback, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import DetailHeader from "../../components/DetailHeader";
import StatusPill from "../../components/StatusPill";
import TagChip from "../../components/TagChip";
import Tabs from "../../components/Tabs";
import useQueryParams from "../../hooks/useQueryParams";
import useResource from "../../hooks/useResource";
import { listDocuments } from "../../api/documents";
import { getTender, linkDocument, unlinkDocument } from "../../api/tenders";
import { dash, formatAmount } from "../../lib/format";
import AttachedDocumentsTab from "../documents/AttachedDocumentsTab";
import AttachedDocumentDrawer from "../documents/AttachedDocumentDrawer";
import TenderOverviewTab from "./TenderOverviewTab";
import TenderCostsTab from "./TenderCostsTab";
import TenderCostDrawer from "./TenderCostDrawer";
import TenderCertificateDrawer from "./TenderCertificateDrawer";
import { TENDER_DOCUMENT_TYPES } from "./tenderFields";
import { paths } from "../../app/routes";

// Qualification criteria and the evidence picker join these in Phase 4.
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "costs", label: "Costs & certificates" },
  { key: "documents", label: "Documents" },
];

// Whichever drawer is open rides in the query string alongside the tab, so
// both are linkable and Back closes the drawer.
const VIEW_SCHEMA = { tab: "overview", cost: "", cert: "", doc: "", type: "" };

export default function TenderDetailPage() {
  const { tenderId } = useParams();
  const navigate = useNavigate();
  const { values, setValues } = useQueryParams(VIEW_SCHEMA);

  const loadTender = useCallback(() => getTender(tenderId), [tenderId]);
  const tender = useResource(loadTender);

  const loadDocuments = useCallback(() => listDocuments(), []);
  const documents = useResource(loadDocuments, { initialData: [] });

  const record = tender.data;

  // Documents belong to a tender through the tender's own id list, until the
  // backend carries a tender_id on the document itself.
  const tenderDocuments = useMemo(() => {
    if (!record) return [];
    const ids = new Set(record.document_ids);
    return documents.data.filter((document) => ids.has(document.id));
  }, [record, documents.data]);

  const openCost = record && values.cost ? record.cost_items.find((c) => c.id === values.cost) : null;
  const openCertificate =
    record && values.cert ? record.certificates.find((c) => c.id === values.cert) : null;
  const openDocument = values.doc
    ? tenderDocuments.find((document) => document.id === values.doc)
    : null;

  const costDrawerOpen = values.cost === "new" || Boolean(openCost);
  const certDrawerOpen = values.cert === "new" || Boolean(openCertificate);
  const docDrawerOpen = values.doc === "new" || Boolean(openDocument);

  function closeDrawers() {
    setValues({ cost: "", cert: "", doc: "", type: "" });
  }

  function handleSaved() {
    closeDrawers();
    tender.reload();
    documents.reload();
  }

  if (tender.loading) return <p className="muted">Loading...</p>;
  if (tender.error) {
    return (
      <div className="card">
        <div className="alert alert-error">{tender.error}</div>
        <Link to={paths.tenders()}>Back to Tenders</Link>
      </div>
    );
  }

  return (
    <>
      <p className="muted page-note">
        <Link to={paths.tenders()}>&larr; Tenders</Link>
      </p>

      <DetailHeader
        title={record.title}
        subtitle={record.issuing_authority}
        facts={[
          { label: "Reference no.", value: dash(record.reference_number) },
          { label: "Estimated value", value: formatAmount(record.estimated_value) },
          { label: "Submission deadline", value: dash(record.submission_deadline) },
          { label: "EMD amount", value: formatAmount(record.emd_amount) },
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
              onClick={() => navigate(paths.editTender(record.id))}
            >
              Edit
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

      <Tabs
        tabs={TABS}
        active={values.tab}
        onChange={(tab) => setValues({ tab, cost: "", cert: "", doc: "", type: "" })}
      />

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

      {values.tab !== "costs" && values.tab !== "documents" && (
        <TenderOverviewTab tender={record} documentCount={tenderDocuments.length} />
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
      {docDrawerOpen && (
        <AttachedDocumentDrawer
          context={{
            subjectName: record.issuing_authority,
            title: record.title,
            contractValue: record.estimated_value,
            department: "",
          }}
          documentTypes={TENDER_DOCUMENT_TYPES}
          document={openDocument}
          presetType={values.type}
          onLink={(documentId) => linkDocument(record.id, documentId)}
          onUnlink={(documentId) => unlinkDocument(record.id, documentId)}
          onClose={closeDrawers}
          onSaved={handleSaved}
        />
      )}
    </>
  );
}

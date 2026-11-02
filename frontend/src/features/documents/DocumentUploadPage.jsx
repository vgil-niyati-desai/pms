import { useState } from "react";
import DocumentForm from "./DocumentForm";

export default function DocumentUploadPage() {
  const [saved, setSaved] = useState(false);

  return (
    <section className="card">
      <h2>Upload a document</h2>
      <p className="muted">
        Upload a PDF, PNG, or JPG. You can add descriptive details, but they do
        not determine account ownership.
      </p>
      {saved && <div className="alert alert-success" role="status">Document uploaded.</div>}
      <DocumentForm
        uploadOnly
        onSaved={() => setSaved(true)}
      />
    </section>
  );
}

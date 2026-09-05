from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime


class DocumentFields(BaseModel):
    """Every field the API returns except the identifier.

    Split out so the identifier, which is a MongoDB ObjectId rendered as a
    string, stays separate from the fields the forms actually collect.

    Everything here is optional. A document row is reachable and editable
    with nothing filled in — the screens render blanks as dashes, and the
    grouping, filtering and evidence logic all skip empty values — so which
    fields *must* be captured is business policy, still unsettled, and is
    left to the forms rather than baked into the API. The entry form keeps
    asking for type, category, client and submitted-by; an import does not
    have to invent them.
    """

    document_type: Optional[str] = None
    category: Optional[str] = None
    client_name: Optional[str] = None
    reference_number: Optional[str] = None
    project_title: Optional[str] = None
    contract_value: Optional[str] = None
    document_date: Optional[str] = None
    department: Optional[str] = None
    submitted_by: Optional[str] = None
    notes: Optional[str] = None
    file_name: Optional[str] = None
    created_at: datetime


class DocumentMongoOut(DocumentFields):
    """MongoDB response shape.

    `id` is the ObjectId rendered as its 24-character hex string. The React
    app only ever compares ids and puts them in URLs, so it needs no change.
    """

    id: str


# ---------------------------------------------------------------------------
# Projects
#
# A project is the record a tender submission cites as past evidence. It owns
# its own fields and points at the documents that prove it; the documents
# themselves stay in the document log, shared with every other area.
# ---------------------------------------------------------------------------


def _required(value: str, label: str) -> str:
    """Reject a field that is blank or only whitespace.

    The form already checks both of these, so this is the backstop for
    anything reaching the API another way — not the primary message a user
    reads.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


class ProjectFields(BaseModel):
    """The fields the project form owns.

    Everything but title and client_name is optional, because a project is
    often logged from a single email and filled in as the paperwork arrives.
    Dates are ISO `YYYY-MM-DD` strings and contract_value is free text, both
    matching what the <input> elements produce — the value is often written
    "5,00,000" and is not arithmetic anyone does here.
    """

    # Only the title is enforced: the list screen renders each project as a
    # link whose text *is* the title, so a blank one would leave a record
    # nobody can see or click — a functional break, not a policy choice.
    # Every other field is display metadata and stays open-ended until the
    # business rules for what must be captured are settled.
    title: str
    client_name: Optional[str] = None
    reference_number: Optional[str] = None
    contract_value: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    department: Optional[str] = None
    scope_summary: Optional[str] = None
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        return _required(value, "Project title")

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: List[str]) -> List[str]:
        """Trim, drop blanks, and de-duplicate case-insensitively.

        The tag box already does this as you type; doing it here too means the
        stored vocabulary stays clean no matter what wrote the record.
        """
        cleaned: List[str] = []
        seen = set()
        for tag in value or []:
            text = str(tag).strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            cleaned.append(text)
        return cleaned


class ProjectIn(ProjectFields):
    """What the project form sends on create and on save.

    Deliberately does not carry id, document_ids or the timestamps: those are
    the server's, and a form post must not be able to rewrite them.
    """


class ProjectOut(ProjectFields):
    """What every project endpoint returns.

    `id` is the ObjectId as its 24-character hex string, the same convention
    the documents endpoints use.
    """

    id: str
    document_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    """One page of results, plus the total the pager needs to size itself."""

    items: List[ProjectOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Employees
#
# An employee record answers a tender's personnel criteria: who we can name,
# what they hold, and which CV version to attach. CVs and certifications are
# embedded in the employee — neither means anything apart from the person —
# while their uploaded files live in the shared document log, referenced by
# document_id.
# ---------------------------------------------------------------------------


class EmployeeFields(BaseModel):
    """The fields the employee form owns.

    experience_years stays a string, like contract_value on a project: it is
    what the <input> produces, and screens display it rather than compute
    with it. The one thing checked here is that a value that *does* parse is
    not negative, mirroring the form's own check.
    """

    # full_name is enforced for the same reason a project's title is: it is
    # the text of the record's link in the list. Nothing else is, including
    # designation — the form still asks for it, an import need not.
    full_name: str
    employee_code: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    date_of_joining: Optional[str] = None
    experience_years: Optional[str] = None
    highest_qualification: Optional[str] = None
    key_skills: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _required(value, "Full name")

    @field_validator("experience_years")
    @classmethod
    def _check_experience(cls, value: Optional[str]) -> Optional[str]:
        if value is None or str(value).strip() == "":
            return value
        try:
            years = float(str(value).strip())
        except ValueError:
            return value
        if years < 0:
            raise ValueError("Total experience cannot be negative.")
        return value


class EmployeeIn(EmployeeFields):
    """What the employee form sends on create and on save.

    Deliberately without cvs, certifications, id or the timestamps: the
    nested records have their own endpoints, and the rest is the server's.
    """


class CvIn(BaseModel):
    """One version of an employee's CV.

    document_id points at the uploaded file in the document log; the frontend
    stores the file through the documents endpoint first and then records the
    pointer here, so this schema never sees the bytes.
    """

    version_label: Optional[str] = None
    purpose: Optional[str] = None
    cv_date: Optional[str] = None
    uploaded_by: Optional[str] = None
    document_id: Optional[str] = None
    file_name: Optional[str] = None


class CvOut(CvIn):
    id: str
    created_at: datetime


class CertificationIn(BaseModel):
    """One certification held by an employee.

    Whether it is valid, expired, or has no expiry is never stored — it is a
    function of expiry_date and today, computed where it is read, so a record
    cannot go stale overnight.
    """

    name: Optional[str] = None
    issuing_body: Optional[str] = None
    certificate_number: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    notes: Optional[str] = None
    document_id: Optional[str] = None
    file_name: Optional[str] = None


class CertificationOut(CertificationIn):
    id: str
    created_at: datetime


class EmployeeOut(EmployeeFields):
    """What every employee endpoint returns: the record with both of its
    nested collections, which is the shape every screen was built around."""

    id: str
    cvs: List[CvOut] = Field(default_factory=list)
    certifications: List[CertificationOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EmployeePage(BaseModel):
    items: List[EmployeeOut]
    total: int
    page: int
    page_size: int


class CertificationRow(CertificationOut):
    """One row of the flattened certification index: the certification plus
    who holds it and its computed validity."""

    employee_id: str
    employee_name: str
    status: str


class CertificationIndexPage(BaseModel):
    items: List[CertificationRow]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Tenders
#
# A tender is a bid being tracked from identification to outcome. It embeds
# its cost items (EMD, fees) and the certificates submitted with it — neither
# means anything apart from the bid — and references attached documents by id
# the way a project does, because the files live in the shared document log.
# ---------------------------------------------------------------------------


def _non_negative_amount(value: Optional[str], label: str) -> Optional[str]:
    """Backstop for a money field: a value that parses must not be negative.
    Unparseable text is left alone — display code shows it as typed."""
    if value is None or str(value).strip() == "":
        return value
    try:
        amount = float(str(value).replace(",", "").strip())
    except ValueError:
        return value
    if amount < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


class TenderFields(BaseModel):
    """The fields the tender form owns.

    Money fields stay the strings the number inputs produce, like a project's
    contract_value; the list endpoint converts them when a range or a sort
    needs arithmetic. Dates are ISO `YYYY-MM-DD` strings, which compare
    correctly as text — the deadline window relies on that.
    """

    # As with projects, only the title is enforced — it is the list link's
    # text. Authority and status are back to optional: a tender with no
    # status simply never matches the Open view, which is data meaning,
    # not a malfunction. The form still asks for all three.
    title: str
    issuing_authority: Optional[str] = None
    reference_number: Optional[str] = None
    tender_type: Optional[str] = None
    estimated_value: Optional[str] = None
    emd_amount: Optional[str] = None
    tender_fee: Optional[str] = None
    published_date: Optional[str] = None
    submission_deadline: Optional[str] = None
    status: Optional[str] = None
    submission_mode: Optional[str] = None
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        return _required(value, "Tender title")

    @field_validator("estimated_value")
    @classmethod
    def _check_value(cls, value: Optional[str]) -> Optional[str]:
        return _non_negative_amount(value, "Estimated value")

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: List[str]) -> List[str]:
        """Same trim/de-duplicate treatment project tags get."""
        cleaned: List[str] = []
        seen = set()
        for tag in value or []:
            text = str(tag).strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            cleaned.append(text)
        return cleaned


class TenderIn(TenderFields):
    """What the tender form sends. No cost items, certificates, document ids
    or timestamps: the nested records have their own endpoints, attachment is
    its own call, and the rest is the server's."""


class CostItemIn(BaseModel):
    """One cost paid to pursue the tender: EMD, tender fee, DD charges.

    document_id points at the payment receipt in the document log, stored
    there first by the frontend, the same arrangement a CV has.
    """

    cost_type: Optional[str] = None
    amount: Optional[str] = None
    payment_mode: Optional[str] = None
    instrument_number: Optional[str] = None
    paid_on: Optional[str] = None
    document_id: Optional[str] = None
    file_name: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, value: Optional[str]) -> Optional[str]:
        # Missing is fine; a value that parses negative is not — the cost
        # total sums whatever parses, and a negative would silently shrink it.
        return _non_negative_amount(value, "Amount")


class CostItemOut(CostItemIn):
    id: str
    created_at: datetime


class TenderCertificateIn(BaseModel):
    """One certificate submitted with the bid."""

    name: Optional[str] = None
    issuing_body: Optional[str] = None
    certificate_number: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    notes: Optional[str] = None
    document_id: Optional[str] = None
    file_name: Optional[str] = None


class TenderCertificateOut(TenderCertificateIn):
    id: str
    created_at: datetime


class TenderOut(TenderFields):
    id: str
    cost_items: List[CostItemOut] = Field(default_factory=list)
    certificates: List[TenderCertificateOut] = Field(default_factory=list)
    document_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TenderPage(BaseModel):
    items: List[TenderOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Manual redaction
#
# A redacted copy is generated and streamed straight back to the browser: it
# is never stored, and the entry it came from is never touched. So there is
# no "redaction" record here, only the request shape and the page list the
# selection UI needs.
# ---------------------------------------------------------------------------


class RedactionArea(BaseModel):
    """One rectangle the user drew, in fractions of its page.

    Normalised rather than absolute so the browser can render the page at any
    size it likes -- a wide window, a phone, a zoomed-in view -- and still
    describe the same region of the document. `page` is zero-based, and is
    always 0 for a PNG or JPG.
    """

    page: int = Field(ge=0)
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class RedactionRequest(BaseModel):
    """Every area to burn into one generated copy.

    The cap is a sanity bound, not a product limit: a few dozen rectangles is
    a heavily redacted document, and anything past this is a malformed or
    hostile request rather than a real selection.
    """

    areas: List[RedactionArea] = Field(min_length=1, max_length=500)


class RedactionPage(BaseModel):
    """One page's size, in its own units, for laying out the selection view."""

    index: int
    width: float
    height: float


class RedactionSource(BaseModel):
    """What the redaction UI needs to know before it can draw anything."""

    kind: str
    file_name: Optional[str] = None
    pages: List[RedactionPage]

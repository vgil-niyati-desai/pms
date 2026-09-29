from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from datetime import date, datetime


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
    # The project this document belongs to, as the project's own id, or
    # None for a document that is not a project's -- a CV, a tender
    # receipt, or a project document logged before it was attached.
    #
    # The project also lists the document in its own document_ids. Both
    # are maintained: this one answers "whose is this?" without reading
    # every project, and that one answers "what does this project hold?"
    # and is still what every existing writer keeps up to date.
    project_id: Optional[str] = None
    # The employee this document belongs to, as the employee's own id, or
    # None for a document that is nobody's -- a project's evidence, a tender
    # receipt, or a CV logged before the reverse pointer existed.
    #
    # The counterpart of project_id above, and maintained the same way. The
    # employee also names the document from the other side, in the CV or
    # certification that points at it; this one answers "whose is this?"
    # without reading every employee.
    employee_id: Optional[str] = None
    # The tender this document belongs to, as the tender's own id, or None
    # for a document that is no tender's. The third of the three owners, and
    # maintained exactly as the two above are: the tender also lists the
    # document in its own document_ids, and this answers "whose is this?"
    # without reading every tender.
    tender_id: Optional[str] = None
    created_at: datetime


class DocumentMongoOut(DocumentFields):
    """MongoDB response shape.

    `id` is the ObjectId rendered as its 24-character hex string. The React
    app only ever compares ids and puts them in URLs, so it needs no change.
    """

    id: str


class DocumentPage(BaseModel):
    """One page of the document log, in the same shape as every other list."""

    items: List[DocumentMongoOut]
    total: int
    page: int
    page_size: int


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
    # The distinct document types this project holds, resolved by the
    # server. Derived, never stored: it is what the list screen draws its
    # evidence pills from, and computing it here is what lets that screen
    # stop downloading the whole document log to work it out for itself.
    document_types: List[str] = Field(default_factory=list)
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

    Experience is held as two *dates*, never as a count of years:

        career_start_date  -> Total Professional Experience
        date_of_joining    -> Experience with VGIL

    A typed number would be wrong the day after it was typed, and the two
    quantities are genuinely different -- someone who joined VGIL in 2021
    after seven years elsewhere has four years with VGIL and eleven in total.
    Conflating them understates every experienced hire against a tender's
    personnel criteria, which is the one thing these records exist to answer.
    The years are computed at read time in the router, so they are right on
    the day they are read and need no upkeep.

    experience_years is the field those two replaced. It is kept so that
    anything already stored survives a round trip, and it is deliberately NOT
    the source of truth for anything: nothing computes from it and no screen
    shows it.
    """

    # full_name is enforced for the same reason a project's title is: it is
    # the text of the record's link in the list. Nothing else is, including
    # designation — the form still asks for it, an import need not.
    full_name: str
    employee_code: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    # The day they joined VGIL, and the day their professional career began.
    # Both ISO (YYYY-MM-DD), which is what <input type="date"> produces and
    # what sorts and compares correctly as a plain string.
    date_of_joining: Optional[str] = None
    career_start_date: Optional[str] = None
    # Superseded by the two dates above. See the class docstring.
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

    @field_validator("date_of_joining", "career_start_date")
    @classmethod
    def _check_date(cls, value: Optional[str]) -> Optional[str]:
        """An experience date has to be a real ISO date, or absent.

        Stricter than the fields it replaces, and deliberately so: these two
        are no longer text someone reads off the screen, they are what the
        displayed years are *calculated* from. A value that does not parse
        would silently produce no experience at all rather than a wrong one,
        which is the kind of blank nobody investigates.

        A future date is allowed — a joining date can legitimately be set for
        someone starting next month — and simply reads as no experience yet.
        """
        if value is None or str(value).strip() == "":
            return value
        text = str(value).strip()
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError("Dates must be in YYYY-MM-DD form.")
        return text

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
    nested collections, which is the shape every screen was built around.

    The two experience figures are *derived*, never stored. They are computed
    from the dates above each time a record is read, so they are correct on
    the day they are read and nobody has to remember to revise them. Both are
    null when the date behind them is missing, which the screens show as a
    dash — an honest "not recorded" rather than a zero that reads as "none".
    """

    id: str
    # Years since career_start_date. What a tender means by "shall have N
    # years' experience".
    total_experience_years: Optional[float] = None
    # Years since date_of_joining. What a tender means by "shall have been a
    # regular employee of the bidder for N years".
    vgil_experience_years: Optional[float] = None
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


# ---------------------------------------------------------------------------
# Qualification criteria
#
# What a tender demands of a bidder, recorded from the tender notice so the
# evidence can later be gathered against it. Embedded in the tender like its
# cost items: a criterion means nothing apart from the bid that states it.
#
# Every criterion carries the same frame -- mandatory or desirable, where in
# the notice it came from, the wording as published -- and a `params` object
# whose shape depends on its `kind`. The published wording is kept beside the
# structured fields because clauses rarely fit a template exactly, and the
# wording is what anyone checking the structured version goes back to.
#
# Amounts here are numbers, in rupees, unlike the strings the tender's own
# money fields hold: a criterion's amount exists to be compared against.
# Nothing is matched yet; these only record the requirements.
# ---------------------------------------------------------------------------

MAX_CRITERIA = 100

# Which group each kind is listed under on the tender.
CRITERION_CATEGORIES = {
    "similar_projects": "technical",
    "project_value": "technical",
    "personnel": "personnel",
    "financial": "financial",
    "certification": "documents",
    "document": "documents",
    "other": "other",
}

_MAX_TEXT = 500
_MAX_LONG_TEXT = 2000
_MAX_YEARS = 60
MAX_REQUIRED_CERTIFICATIONS = 50


class _Strict(BaseModel):
    """The rules every criterion model shares.

    Unknown fields are refused rather than silently stored. Types are strict:
    "3" is not a count and `true` is not an amount, so a value of the wrong
    type is refused instead of converted into something nobody entered --
    except a whole number where a decimal is expected, which is the same
    number. Infinity and NaN are refused: they are not amounts or years, and
    JSON cannot carry them back out, so one stored would read back as blank.
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


# Messages from the validators below name no field: the error handling at the
# bottom of this section puts the field's label in front of every message, so
# each reads "Role: is required" rather than a bare "is required".


def _clean_text(value: Optional[str], limit: int = _MAX_TEXT) -> Optional[str]:
    """Trimmed text, None when blank, refused when over the limit."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"must be at most {limit} characters")
    return text


def _clean_list(values: List[str], limit: int) -> List[str]:
    """Trim, drop blanks, de-duplicate case-insensitively -- as tags are --
    then refuse more than `limit` entries. Counted after cleaning, so a list
    padded with blanks or repeats is judged by what would be stored."""
    cleaned: List[str] = []
    seen = set()
    for position, value in enumerate(values or [], start=1):
        text = str(value).strip()
        if not text or text.lower() in seen:
            continue
        if len(text) > _MAX_TEXT:
            raise ValueError(f"entry {position} must be at most {_MAX_TEXT} characters")
        seen.add(text.lower())
        cleaned.append(text)
    if len(cleaned) > limit:
        raise ValueError(f"must list at most {limit} entries")
    return cleaned


def _required_text(value: Optional[str]) -> str:
    text = _clean_text(value)
    if not text:
        raise ValueError("is required")
    return text


class SimilarProjectsParams(_Strict):
    count: int = Field(ge=1, le=1000)
    work_description: Optional[str] = None
    min_value_each: Optional[float] = Field(default=None, ge=0)
    completed_within_years: Optional[float] = Field(default=None, gt=0, le=_MAX_YEARS)
    must_be_completed: bool = False

    @field_validator("work_description")
    @classmethod
    def _text(cls, value):
        return _clean_text(value)


class ProjectValueParams(_Strict):
    min_value: float = Field(gt=0)
    # One project of at least this value, the total of the cited projects,
    # or their average.
    basis: Literal["single", "total", "average"] = "single"
    within_years: Optional[float] = Field(default=None, gt=0, le=_MAX_YEARS)


class PersonnelParams(_Strict):
    role: str
    count: int = Field(default=1, ge=1, le=1000)
    min_experience_years: Optional[float] = Field(default=None, ge=0, le=_MAX_YEARS)
    # Which of the two experience figures an employee record holds the
    # requirement is about: total professional, or with VGIL.
    experience_basis: Literal["total", "vgil"] = "total"
    qualification: Optional[str] = None
    required_certifications: List[str] = Field(default_factory=list)

    @field_validator("role")
    @classmethod
    def _role(cls, value):
        return _required_text(value)

    @field_validator("qualification")
    @classmethod
    def _text(cls, value):
        return _clean_text(value)

    @field_validator("required_certifications")
    @classmethod
    def _list(cls, value):
        return _clean_list(value, MAX_REQUIRED_CERTIFICATIONS)


class CertificationParams(_Strict):
    """A certificate the bidding company must hold, e.g. ISO 9001. Not one of
    the certificates *submitted* with the bid, which the tender lists
    separately."""

    name: str
    issuing_body: Optional[str] = None
    # The date it must still be valid on. Left empty, the submission date.
    valid_on: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        return _required_text(value)

    @field_validator("issuing_body")
    @classmethod
    def _text(cls, value):
        return _clean_text(value)

    @field_validator("valid_on")
    @classmethod
    def _date(cls, value):
        text = _clean_text(value)
        if text is None:
            return None
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError("must be a date in YYYY-MM-DD form")
        return text


class DocumentParams(_Strict):
    document_type: str
    description: Optional[str] = None
    count: Optional[int] = Field(default=None, ge=1, le=1000)
    validity_note: Optional[str] = None

    @field_validator("document_type")
    @classmethod
    def _type(cls, value):
        return _required_text(value)

    @field_validator("description", "validity_note")
    @classmethod
    def _text(cls, value):
        return _clean_text(value)


class FinancialParams(_Strict):
    metric: Literal[
        "average_annual_turnover", "net_worth", "solvency", "working_capital", "other"
    ]
    min_amount: float = Field(ge=0)
    # Over how many financial years, e.g. the average of the last three.
    period_years: Optional[int] = Field(default=None, ge=1, le=20)
    # What the figure is, when the metric is "other".
    description: Optional[str] = None

    @field_validator("description")
    @classmethod
    def _text(cls, value):
        return _clean_text(value)

    @model_validator(mode="after")
    def _describe_other(self):
        if self.metric == "other" and not self.description:
            # A rule across two fields, so it names the one to fill in itself.
            raise ValueError("Describe the requirement: required when the requirement is Other")
        return self


class OtherParams(_Strict):
    description: str

    @field_validator("description")
    @classmethod
    def _text(cls, value):
        text = _clean_text(value, _MAX_LONG_TEXT)
        if not text:
            raise ValueError("is required")
        return text


class _CriterionFrame(_Strict):
    """What every criterion carries, whatever its kind."""

    mandatory: bool = True
    clause_ref: Optional[str] = None
    source_text: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("clause_ref")
    @classmethod
    def _ref(cls, value):
        return _clean_text(value, 200)

    @field_validator("source_text", "notes")
    @classmethod
    def _long(cls, value):
        return _clean_text(value, _MAX_LONG_TEXT)


class SimilarProjectsCriterion(_CriterionFrame):
    kind: Literal["similar_projects"]
    params: SimilarProjectsParams


class ProjectValueCriterion(_CriterionFrame):
    kind: Literal["project_value"]
    params: ProjectValueParams


class PersonnelCriterion(_CriterionFrame):
    kind: Literal["personnel"]
    params: PersonnelParams


class CertificationCriterion(_CriterionFrame):
    kind: Literal["certification"]
    params: CertificationParams


class DocumentCriterion(_CriterionFrame):
    kind: Literal["document"]
    params: DocumentParams


class FinancialCriterion(_CriterionFrame):
    kind: Literal["financial"]
    params: FinancialParams


class OtherCriterion(_CriterionFrame):
    kind: Literal["other"]
    params: OtherParams


# What a criterion POST or PUT carries. `kind` picks the params model, so an
# unknown kind, a missing required field or a stray one is a 422. id,
# category and the timestamps are the server's and are refused if sent.
CriterionIn = Annotated[
    Union[
        SimilarProjectsCriterion,
        ProjectValueCriterion,
        PersonnelCriterion,
        CertificationCriterion,
        DocumentCriterion,
        FinancialCriterion,
        OtherCriterion,
    ],
    Field(discriminator="kind"),
]


_criterion_adapter = TypeAdapter(CriterionIn)

# The label each field goes by in the criterion drawer, so an error names the
# field the way the person filling it in sees it. Where one name means
# different things in different kinds -- count, description -- the kind picks.
_FIELD_LABELS = {
    "kind": "Type",
    "mandatory": "Requirement (mandatory or desirable)",
    "clause_ref": "Clause reference",
    "source_text": "Wording in the tender",
    "notes": "Internal notes",
    "params": "Details",
    "work_description": "Nature of the work",
    "min_value_each": "Minimum value of each",
    "completed_within_years": "Within the last (years)",
    "must_be_completed": "Must be completed, not ongoing",
    "min_value": "Minimum value",
    "basis": "Measured as",
    "within_years": "Within the last (years)",
    "role": "Role / designation",
    "min_experience_years": "Minimum experience (years)",
    "experience_basis": "Experience counted as",
    "qualification": "Qualification",
    "required_certifications": "Certifications held",
    "name": "Certification",
    "issuing_body": "Issuing body",
    "valid_on": "Must be valid on",
    "document_type": "Document type",
    "validity_note": "Validity",
    "metric": "Requirement",
    "min_amount": "Minimum amount",
    "period_years": "Over the last (financial years)",
}
_KIND_FIELD_LABELS = {
    ("similar_projects", "count"): "Number of similar projects",
    ("personnel", "count"): "Number of people",
    ("document", "count"): "How many",
    ("document", "description"): "Description",
    ("financial", "description"): "Describe the requirement",
    ("other", "description"): "Requirement",
}

# Pydantic's wording, where plainer wording says the same thing.
_ERROR_TEXT = {
    "missing": "is required",
    "extra_forbidden": "is not an accepted field",
    "finite_number": "must be a finite number, not Infinity or NaN",
    "int_type": "must be a whole number",
    "float_type": "must be a number",
    "bool_type": "must be true or false",
    "string_type": "must be text",
    "list_type": "must be a list",
    "dict_type": "must be an object",
}


# Range errors, as "must be at least 1" rather than pydantic's "Input should
# be greater than or equal to 1". The bound comes from the error's context.
_BOUND_TEXT = {
    "greater_than_equal": ("ge", "must be at least"),
    "greater_than": ("gt", "must be more than"),
    "less_than_equal": ("le", "must be at most"),
    "less_than": ("lt", "must be less than"),
}


def _criterion_error(error: Dict[str, Any], kind: Optional[str]) -> Dict[str, Any]:
    """One pydantic error as FastAPI would report it, with the field's label
    leading the message."""
    loc = list(error.get("loc") or ())
    # The discriminated union puts the matched kind first; it is not a field.
    if kind and loc and loc[0] == kind:
        loc = loc[1:]
    kind_known = kind if kind in CRITERION_CATEGORIES else None
    entry = loc[-1] if loc and isinstance(loc[-1], int) else None
    names = [part for part in loc if isinstance(part, str)]
    field = names[-1] if names else None

    error_type = error.get("type", "")
    if error_type.startswith("union_tag"):
        label, text = "Type", (
            "is required" if error_type == "union_tag_not_found"
            else "must be one of " + ", ".join(sorted(CRITERION_CATEGORIES))
        )
    elif error_type == "extra_forbidden":
        label, text = f"'{field}'", _ERROR_TEXT[error_type]
    else:
        label = _KIND_FIELD_LABELS.get((kind_known, field)) or _FIELD_LABELS.get(field)
        if label is None:
            label = (field or "Details").replace("_", " ").capitalize()
        bound = _BOUND_TEXT.get(error_type)
        ctx = error.get("ctx") or {}
        text = _ERROR_TEXT.get(error_type)
        if bound and bound[0] in ctx:
            limit = ctx[bound[0]]
            if isinstance(limit, float) and limit.is_integer():
                limit = int(limit)
            text = f"{bound[1]} {limit}"
        elif error_type == "literal_error" and str(error.get("msg", "")).startswith("Input should be "):
            text = "must be " + str(error["msg"])[len("Input should be "):]
        elif text is None:
            text = str(error.get("msg", "is not valid"))
            if text.startswith("Value error, "):
                text = text[len("Value error, "):]
            if field == "params" and ":" in text:
                # A rule across fields already names the one it is about.
                label, text = (part.strip() for part in text.split(":", 1))
            else:
                text = text[:1].lower() + text[1:]
        if entry is not None:
            label = f"{label} (entry {entry + 1})"

    return {
        "type": error_type,
        "loc": ["body", *error.get("loc", ())],
        "msg": f"{label}: {text}",
    }


class CriterionValidationError(Exception):
    """A criterion body that failed validation, with its labelled errors in
    the shape FastAPI's own 422 uses."""

    def __init__(self, errors: List[Dict[str, Any]]):
        super().__init__(errors)
        self.errors = errors


def validate_criterion(data: Any) -> Any:
    """Validate a criterion body, raising CriterionValidationError with one
    labelled message per problem."""
    try:
        return _criterion_adapter.validate_python(data)
    except ValidationError as exc:
        kind = data.get("kind") if isinstance(data, dict) else None
        raise CriterionValidationError(
            [_criterion_error(error, kind) for error in exc.errors(include_url=False)]
        )


class CriterionOut(BaseModel):
    """A stored criterion. `params` is returned as stored; its shape was
    checked against `kind` on the way in."""

    id: str
    kind: str
    category: str
    mandatory: bool = True
    clause_ref: Optional[str] = None
    source_text: Optional[str] = None
    notes: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class TenderOut(TenderFields):
    id: str
    cost_items: List[CostItemOut] = Field(default_factory=list)
    certificates: List[TenderCertificateOut] = Field(default_factory=list)
    # Deliberately absent from TenderIn: criteria change through their own
    # endpoints, so saving the tender form can never drop them.
    criteria: List[CriterionOut] = Field(default_factory=list)
    document_ids: List[str] = Field(default_factory=list)
    # The past projects this bid cites as experience. Ids of existing
    # projects, which the tender points at and does not own: a project is
    # evidence in its own right and may be cited by any number of bids.
    #
    # Deliberately absent from TenderIn, like document_ids: citing is its own
    # call, so a form posted from a stale page cannot drop evidence someone
    # added in the meantime.
    cited_project_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TenderPage(BaseModel):
    items: List[TenderOut]
    total: int
    page: int
    page_size: int


class CandidateOut(BaseModel):
    """One record that passed a criterion's rules (app/matching.py), with the
    reasons it passed and what could not be checked. Computed on request and
    never stored."""

    type: Literal["project", "employee", "document"]
    id: str
    title: str
    subtitle: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    # A project already cited by this tender / a document already attached.
    cited: bool = False
    attached: bool = False
    file_name: Optional[str] = None
    owner_type: Optional[str] = None
    owner_id: Optional[str] = None
    owner_label: Optional[str] = None


class CandidateResults(BaseModel):
    """The candidates for one criterion. `mode` is "matched" when rules were
    applied and "manual" when the criterion cannot be matched from recorded
    data; `notes` say what was not, or could not be, checked."""

    criterion_id: str
    kind: str
    mode: Literal["matched", "manual"]
    summary: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    candidates: List[CandidateOut] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


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


# ---------------------------------------------------------------------------
# Automatic detection of sensitive figures
#
# A suggestion, never a redaction. The scan reads the stored file and returns
# rectangles in the *same* normalised format as RedactionArea above, so a box
# the user accepts is posted to /redacted-copy exactly as a hand-drawn one is.
# Nothing here is stored, and the document is not modified by looking at it.
# ---------------------------------------------------------------------------


class SensitiveDetection(BaseModel):
    """One figure the scan thinks should probably be covered.

    The geometry is a RedactionArea in all but name -- deliberately, so the
    review UI can draw a suggestion and a manual box on the same surface and
    the redaction endpoint cannot tell which was which. The rest is review
    material: what was matched, what to call it, and how sure the rule was.
    """

    id: str
    page: int = Field(ge=0)
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    category: str
    label: str
    text: str
    confidence: str
    rule: str
    # "text" or "ocr" -- how the words under this box were read. A figure
    # Tesseract lifted off a scan is worth checking against the page in a way
    # one taken from a content stream is not, and the reviewer can only make
    # that call if the suggestion says which it was.
    source: str = "text"


class SensitiveScan(BaseModel):
    """Everything one pass over a document found, and what it could not read.

    Reading is per page, so the report is too. A PDF can be text throughout,
    scanned throughout, or a mix of the two, and `text_pages` / `ocr_pages`
    say which page went which way -- an OCR'd page's suggestions are worth a
    closer look than a text page's, and the reviewer can only know that if it
    is said.

    `pages_without_text` and `pages_skipped` are the honest part: a page that
    could not be read, or that OCR never got to, is reported as such rather
    than folded into an empty result that would read as "nothing sensitive
    here". `engine` is "text", "ocr", "mixed" or "none" for the document as a
    whole.
    """

    kind: str
    engine: str
    ocr_available: bool
    detections: List[SensitiveDetection]
    pages_scanned: int
    text_pages: List[int] = []
    ocr_pages: List[int] = []
    pages_without_text: List[int]
    pages_skipped: List[int] = []
    truncated: bool
    message: Optional[str] = None

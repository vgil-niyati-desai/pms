# Procurement Management System

Internal tool for keeping the projects, people, documents and tenders a bid
is assembled from in one place, so the evidence a tender asks for is ready
to pull from instead of being chased down each time.

Stack: React (Vite) frontend, FastAPI backend, MongoDB database (via
pymongo). Runs entirely on internal/local infrastructure — no external or
cloud services involved.

> Start with [`README.md`](README.md): prerequisites, quick start,
> configuration, tests and the Linux deployment notes. This file is the
> detailed reference for how each area works and for the operator tools.

MongoDB is the only database. Document metadata and uploaded files are
submitted from the React forms, saved through the FastAPI backend, and stored
in MongoDB (records) and `backend/app/uploads/` (files). Every area has its
own endpoints and collection in the same database: documents, projects,
employees, tenders, the proposal library, users, sessions and login throttling.
The browser keeps only one thing locally: the "Added by" name last used on a
document form (in `localStorage`).

## Folder structure

```
procurement-management-system/
├── README.md        overview, quick start, configuration, deployment notes
├── SETUP.md         this file: detailed reference
├── backend/         FastAPI app (Python)
│   ├── app/
│   │   ├── main.py          entry point; mounts the routers
│   │   ├── mongodb.py       MongoDB connection, collection handles, index setup
│   │   ├── storage.py       uploaded-file handling, shared by every router
│   │   ├── schemas.py       request/response validation
│   │   ├── redaction.py     manual PDF redaction
│   │   ├── detection.py     automatic sensitive-figure detection (OCR)
│   │   ├── auth.py          login, sessions, CSRF, roles, user accounts
│   │   ├── rate_limit.py    login rate limiting (login_throttle)
│   │   ├── create_admin.py  one-off bootstrap of the first Admin account
│   │   ├── docx_text.py     text out of .docx/.rtf write-ups
│   │   ├── proposal_library.py  builds a library record from a write-up file
│   │   ├── employee_excel_import.py, employee_identity.py  employee workbook import
│   │   ├── uploads/         uploaded files, named by UUID
│   │   └── routers/
│   │       ├── documents_mongo.py          document log endpoints
│   │       ├── company_documents_mongo.py  company certificates and registrations
│   │       ├── proposal_library_mongo.py   reusable proposal write-ups
│   │       ├── projects_mongo.py           project endpoints
│   │       ├── employees_mongo.py          employee/CV endpoints
│   │       └── tenders_mongo.py            tender endpoints
│   ├── migrations/          numbered one-off migrations, run by migrate.py
│   ├── tests/               unittest suites; mongomock, no MongoDB needed
│   ├── ocr/tessdata/        OCR language data
│   ├── migrate.py           migration runner
│   ├── import_company_documents.py   bulk import of company documents (3g)
│   ├── import_proposal_library.py    bulk import of proposal write-ups (3h)
│   ├── import_tender_documents.py    bulk import of tenders and their documents (3i)
│   ├── verify_*.py          older end-to-end checks of each area's endpoints
│   ├── diagnose_detection.py  explains why a figure was or was not suggested (3f)
│   ├── run.ps1              Windows launcher that uses the venv's Python
│   ├── requirements.txt
│   ├── requirements-dev.txt mongomock/httpx: tests/ and the --offline runs
│   └── .env.example         copy to .env and adjust
├── frontend/        React app (Vite)
│   ├── tests/               node:test suites
│   └── src/
│       ├── app/          shell, sidebar, routes, auth context
│       ├── components/   shared UI (table, drawer, modal, fields, previews)
│       ├── features/     one folder per area: projects, cvs, tenders, documents,
│       │                 company, library, users, dashboard
│       ├── api/          one module per resource; http.js holds the shared
│       │                 base URL, CSRF handling and error reading
│       └── index.css     the whole visual system, driven by tokens in :root
└── scripts/         standalone operator tools (not part of the app)
    ├── google_drive_readonly.py  lists Drive metadata after browser OAuth
    ├── llmthing_ocr.py           optional OCR helper for scanned certificates (3g)
    ├── build_company_package.py  builds a company-document import package (3g)
    ├── build_tender_package.py   builds a tender-documents import package (3i)
    └── pdf_splitter/  splits a combined PDF using an Excel index
```

## 1. Backend setup

Open a terminal in the `backend` folder:

```
python -m venv venv
venv\Scripts\activate          (Windows)
pip install -r requirements.txt
```

Copy `.env.example` to `.env`:

```
copy .env.example .env
```

The MongoDB defaults in it (`localhost`, port `27017`, database
`doc_collection_dev`, no username or password) match a default local MongoDB
install, so there is usually nothing to edit. Leave `MONGO_USER` and
`MONGO_PASSWORD` empty unless you turned authentication on.

You do **not** need to create the database or any collection by hand. MongoDB creates both the first time an entry is saved,
and the backend creates its indexes on startup.

## 1b. MongoDB setup

MongoDB must be running somewhere the backend can reach. For local
development on Windows, install the free MongoDB Community Server:

1. Download it from <https://www.mongodb.com/try/download/community>
   (Platform: Windows, Package: msi).
2. Run the installer. Keep **"Install MongoDB as a Service"** ticked — that
   makes it start automatically with Windows, so you never have to remember
   to launch it. The default port is `27017`, which is what `.env` expects.
3. Installing **MongoDB Compass** when offered is worth it: it is a GUI for
   browsing the data, the equivalent of using `psql` to check a row landed.

Check the service is running (PowerShell):

```
Get-Service MongoDB
```

`Status` should read `Running`. If it says `Stopped`, start it with:

```
Start-Service MongoDB
```

Then confirm the backend can actually reach it:

```
.\venv\Scripts\python.exe verify_mongo.py
```

This drives every endpoint — save, list, search, filter, download, edit,
replace file, duplicate rejection, delete — against a throwaway database
called `doc_collection_dev_verify`, then drops it. Your real database is
never written to. Every line should read `PASS`.

The projects, employees and tenders endpoints have their own suites, run
the same two ways:

```
.\venv\Scripts\python.exe verify_projects.py
.\venv\Scripts\python.exe verify_employees.py
.\venv\Scripts\python.exe verify_tenders.py
```

It drives create, read, edit, delete, attach and detach, plus everything the
list screen asks the server to do that the browser store used to do in
JavaScript: search, the client / status / tag / has-document filters, sorting,
and paging. It uses a throwaway database called
`doc_collection_dev_verify_projects` and drops it afterwards.

If MongoDB isn't up yet, either script can still check the application
code on its own, using an in-memory stand-in:

```
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe verify_mongo.py --offline
```

Run the authentication and login rate-limiting tests without MongoDB (they
use mongomock):

```
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

The backend deliberately still starts when MongoDB is unreachable — it
prints a warning, `http://127.0.0.1:8000/docs` still loads, and document
requests return a `503` explaining what to fix, rather than the whole app
refusing to boot.

## Read-only Google Drive listing

The standalone `scripts/google_drive_readonly.py` tool opens a browser for
Google OAuth and requests the `drive.metadata.readonly` and `drive.readonly`
scopes. The current CLI lists file/folder names, IDs, MIME types, and parent
folder IDs; it does not read or download file contents. The Desktop OAuth
client JSON is read directly from
`C:\Users\HP\Downloads\client_secret_519613123942-l1no88nf9vg68akslms6iujav1k4kfjh.apps.googleusercontent.com.json`.

Install the backend requirements if they have not already been installed,
then run this from the `backend` folder:

```
.\venv\Scripts\python.exe ..\scripts\google_drive_readonly.py --limit 5
```

The OAuth token is stored under `%LOCALAPPDATA%\PMS\GoogleDrive`, outside the
repository. Do not copy the client JSON or token into the project. The tool
prints only Drive metadata and safe error messages, never OAuth credentials
or tokens.

## 1c. Authentication setup

Authentication uses the same MongoDB database selected by `MONGO_DB_NAME`,
with separate `users`, `sessions` and `login_throttle` collections. Startup
creates indexes for those three collections only; no existing document,
project, employee, or tender records are migrated or rewritten.

The same indexes also have migrations, so an existing database can be brought
up to date and checked before the application is started against it:
`0007_login_throttle_indexes` creates the `login_throttle` TTL index, and
`0008_auth_indexes` creates `ux_users_username` (unique),
`ux_sessions_token_hash` (unique) and the `ix_sessions_expires_at` TTL index.
Before creating anything, 0008 checks for usernames shared by more than one
account, session token hashes shared by more than one session, more than one
session record with no token hash (exactly one is normal: the account-change
lock record), and existing indexes that clash by name or key. If it finds any,
it stops the run with the list, writes nothing, and stays pending until they
are resolved by hand. Run `--dry-run` first to see that list without writing
(section 3b-i). Startup creates the same indexes without these checks, and a
clash there stops the backend from starting.

For local HTTP development, keep the local-only setting from `.env.example`:

```
PMS_COOKIE_SECURE=false
PMS_COOKIE_SAMESITE=strict
PMS_SESSION_HOURS=8
```

Create the first local administrator account from the `backend` folder after
copying `.env.example` to `.env` and starting local MongoDB:

```
.\venv\Scripts\python.exe -m app.create_admin
```

The command prompts for the username and password, stores only a PBKDF2
password hash, and refuses a remote MongoDB target or a database not named
with a `_dev`, `_local`, or `_test` suffix by default. It creates an account
marked `Admin`. Admins can then create accounts, assign the `Admin`, `HOD`,
or `Normal User` role, enable or disable accounts, and revoke their sessions
from the application's User management page. The API enforces role
permissions independently of the frontend.

Admins and HODs can use the Documents, Company Documents and Proposal Library
workflows; HODs cannot replace files and can delete only documents with no
references. Only Admins can use Projects, Employees/CVs, Tenders, or User
management. Normal Users can upload documents but cannot list, view, download,
edit, replace, delete, or redact documents, and have no access to Company
Documents or the Proposal Library.
Uploads do not yet record an authenticated owner; user-entered names are not
used to grant ownership-based access. The optional `--allow-remote` mode
requires an explicit typed database-name confirmation and should only be
used by an operator after verifying the intended server target. This setup
command has not been run against a server database.

### Login rate limiting

Failed logins are counted in the `login_throttle` collection, keyed by
HMAC-SHA256 of the client IP and of the username, so neither is ever stored in
plain text. Three dimensions: per IP, and per IP+username, are hard limits that
answer `429` with a `Retry-After` header; per username alone is soft — it only
adds a small, capped delay and never blocks, so attempts spread across many IPs
cannot lock the real owner out. Old counting windows expire on their own
through a TTL index on `window_expires_at`, created both by backend startup and
by migration `0007_login_throttle_indexes`.

Set `PMS_RATE_LIMIT_SECRET` (at least 32 characters) in `backend/.env` before
running more than one worker or instance. Left unset, each process generates
its own random secret and logs a warning on startup, and separate processes'
counters will not agree with each other. The thresholds all have working
defaults and are listed, commented out, in `.env.example`.

The IP counted is the ASGI peer address only: `X-Forwarded-For` and `X-Real-IP`
are deliberately ignored, because a client can set them and nothing here is
configured to trust a proxy that strips them. Behind a reverse proxy, every
request therefore falls in the one bucket belonging to the proxy's own address,
which makes the per-IP limit coarse — a deployment that needs per-client limits
needs a trusted-proxy configuration first. The per-IP+username and per-username
dimensions are unaffected.

The repository contains no production deployment or TLS/reverse-proxy
configuration, so the production topology still needs to be confirmed by the
deployment owner. For an HTTPS deployment, set `PMS_COOKIE_SECURE=true` and
keep `PMS_COOKIE_SAMESITE=strict`. Prefer serving the frontend and API from
the same site, with a reverse proxy routing API paths to FastAPI. Set
`VITE_API_BASE_URL` at frontend build time to the API origin; an empty value
uses the frontend origin. If frontend and API are on different origins, set
`PMS_ALLOWED_ORIGINS` on the backend to the exact frontend origin(s) and
configure the deployed API URL. Do not disable Secure cookies to compensate
for an incorrect TLS or proxy setup. `SameSite=None` is accepted only when
Secure cookies are enabled.

## 1d. Start the backend

```
.\run.ps1
```

`run.ps1` calls the venv's Python directly, so it works whether or not the
venv is activated. Use `.\run.ps1 -Port 8001` to run on a different port.

If you'd rather start it by hand, **activate the venv first** — the venv must
be active in that terminal:

```
venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```


> **`ModuleNotFoundError: No module named 'fastapi'`?**
> This machine has a second `uvicorn.exe` on the system PATH, belonging to the
> global Python 3.11 install, and that one does not have fastapi installed.
> Running a bare `uvicorn ...` without activating the venv picks up the global
> one and fails. Either activate the venv (you should see `(venv)` at the start
> of your prompt) or just use `.\run.ps1`. Check which one you're getting with
> `Get-Command uvicorn` — it should point inside `backend\venv\Scripts`.

You should see it running at `http://127.0.0.1:8000`. Visit
`http://127.0.0.1:8000/docs` in a browser — FastAPI auto-generates an
interactive API page there, useful for testing the backend on its own
before the frontend is involved.

## 2. Frontend setup

Open a **second** terminal, in the `frontend` folder:

```
npm install
npm run dev
```

For local development, copy `.env.example` to `.env.local` if the API is not
running at `http://localhost:8000`, and set `VITE_API_BASE_URL` to its URL.
This starts the React app, usually at `http://localhost:5173`. Open that
in a browser.

Sign in with the local administrator account created in section 1c.

The frontend has its own checks, run from the `frontend` folder. Neither needs
the backend or MongoDB running:

```
npm test        (the node:test suites in frontend/tests/)
npm run lint    (oxlint)
```

## 3. Check that it works

1. With both servers running, sign in and open **Documents**.
2. Click **+ Add document**, fill in the required fields (Document type,
   Category, Client name, Added by), attach a PDF, PNG or JPG, and save.
3. The entry should appear in the table straight away — that confirms the
   round trip (React → FastAPI → MongoDB → back to React). Click the row to
   edit it, **View** to preview the file, and **Delete** to remove it again.
4. To look at the database itself, open MongoDB Compass, or from a terminal:
   ```
   mongosh
   use doc_collection_dev
   db.documents.find().pretty()
   ```
5. `http://127.0.0.1:8000/health` reports whether the backend is currently
   connected to MongoDB — the quickest way to tell a database problem apart from
   a frontend one.

## 3b. What the Projects backend added

Projects are the records a tender submission cites as past evidence. They live
in their own `projects` collection in the same database (set by
`MONGO_PROJECTS_COLLECTION` in `.env`), created automatically on first save
like `documents` was.

```
GET    /projects/            one page: filter, sort, search, page
POST   /projects/            create                              -> 201
GET    /projects/{id}        one project
PUT    /projects/{id}        replace the editable fields
DELETE /projects/{id}        delete the project                  -> 204
GET    /projects/clients     distinct client names, for the filter dropdown
GET    /projects/tags        the tag vocabulary in use
POST   /projects/{id}/documents/{document_id}    attach a document
DELETE /projects/{id}/documents/{document_id}    detach a document
```

Three things worth knowing:

- **A project points at its documents; it does not own them.** Deleting a
  project leaves its documents in the document log, which is what the
  confirmation dialog promises. A document is evidence in its own right and
  may be cited by a tender that has nothing to do with that project.
- **The list endpoint does the work the screen used to do.** The screen now
  receives one page and cannot sort or filter what it was not sent, so
  search, the four filters, the sort and the paging are all query parameters.
  The "has document" filter joins projects to the document log on the server.
- **Attaching is its own endpoint, not a field on PUT.** The server appends
  to the list in one operation, so a form submitted from a stale page cannot
  drop a document someone attached in the meantime.

Anything saved into the old browser-local Projects store is still sitting in
that browser under the key `pms.projects.v1`. It is not read any more and is
not migrated — those records were only ever visible in the one browser that
made them. Say the word if you want a one-off import written.

## 3b-i. The document -> project link, and migration 0001

Until now the only record of which project a document belonged to was the
project's own `document_ids` array. Answering "which documents does this
project hold?" meant downloading every document in the log and filtering it in
the browser, so opening any project paid for every CV, tender receipt and
unattached entry in the system.

A document now also carries `project_id`, holding the project's existing id
(or `null` for a document that belongs to no project — a CV, a tender
receipt, an entry logged on its own). Two endpoints follow from it:

```
GET /projects/{id}/documents     that project's documents, newest first
```

and every project in `GET /projects/` and `GET /projects/{id}` now carries a
derived `document_types` array — the distinct types it holds — which is what
the list screen's evidence pills read.

**Both representations are kept.** `document_ids` has not been removed and is
still written by every path that attaches or detaches, because it is what the
existing data, the "has document" filter and the verification suites all rely
on. `project_id` is the addition, not the replacement. Keeping the two in step
is deliberate for this transition:

- creating a document with `project_id` also adds it to that project's
  `document_ids`, in the same request
- attaching sets both; detaching clears both. Attaching is idempotent, and
  attaching a document another live project already holds is refused with a
  **409** naming that project rather than moving it — the Add Document drawer
  offers "Attach existing" alongside "Upload new", and that is what stops it
  quietly taking a document out of a project still showing it
- deleting a document pulls its id from any project referencing it, so a
  delete from the document log screen can no longer leave a dangling id
- deleting a project clears `project_id` on its documents, which stay in the
  log with their files

Reads accept either: `GET /projects/{id}/documents` returns a document that
carries the `project_id` **or** that the project lists, so nothing disappears
if one of the two writes did not land.

### Running the migration

Existing databases need `project_id` populated once. From `backend/`:

```powershell
.\venv\Scripts\python.exe migrate.py --status     # what has been applied
.\venv\Scripts\python.exe migrate.py --dry-run    # report only, writes nothing
.\venv\Scripts\python.exe migrate.py              # apply
```

`0001_document_project_id` sets `project_id` from the existing
`document_ids`, creates `ix_documents_project_id`, and records itself in a
`migrations` collection so a second run is a no-op. It works in place — it
never copies or replaces a database — and it touches only `documents`.

It writes no document's `project_id` where the answer would be a guess. A
document two projects both reference, a reference pointing at a document that
is not there, or a document already holding a different project's id are all
**reported and left exactly as they are**, for a person to settle. The run
prints what it skipped.

`0002_standardise_contract_type` renames the document type
`Contract (Consultant Services)` to `Contract`. Two imported documents carried
the long spelling, which nothing matched: the evidence strip, the has-document
filter and the type dropdown have always said `Contract`, so the two projects
holding them showed Contract as missing and could never read as fully
evidenced. It changes that one field value and nothing else — no document is
created, deleted or re-id'd, no file is touched, and the project links are
neither read nor written.

`0003_document_employee_id` does for employees what 0001 did for projects:
it sets `employee_id` on a document from the `document_id` each CV and
certification already carried, and creates `ix_documents_employee_id`. It
reads the employee's nested records only, refuses the same four ambiguous
cases 0001 refuses, and does not claim a document that is already a
project's.

`0004_document_tender_id` and `0005_tender_cited_project_ids` are described
in sections 3d-i and 3d-ii, `0006_document_redacted_from_id` in section 3e-i,
and `0007_login_throttle_indexes` and `0008_auth_indexes` in section 1c.

How the runner decides what to do:

- It works on whatever `MONGO_*` resolves to. Variables already set in the
  shell override `backend/.env`. It connects before printing the target, so
  check the target first without connecting:
  `.\venv\Scripts\python.exe -c "from app import mongodb; print(mongodb.safe_uri(), mongodb.MONGO_DB_NAME)"`
- `--status` reads only the `migrations` collection: "applied" means a
  migration's record is there, not that its index or field exists. An index
  that backend startup already created still shows its migration as
  `pending` until the migration runs; running it then creates nothing and
  records it. `--status` and `--dry-run` write nothing.
- A plain run applies pending migrations in order and records each as soon
  as it finishes. If one raises, the run stops there: earlier ones stay
  recorded, that one and later ones stay pending.
- `--force` re-runs **every** migration, 0001 onwards, not just one; there
  is no way to pick a single migration. Do not use it on a shared database
  without a backup and a specific reason.

Other types that are *not* renamed, deliberately: `Agreement`,
`Acceptance of Tender-cum-Order`, `Acceptance of Quotation-cum-Order`,
`Cover / Tender Notice`, and the CV and certificate types. Whether any of
those should fold into an evidence type is a question about the business
vocabulary, not something a rename should decide; the run lists them so it is
clear what it left alone.

## 3c. What the Employees/CV backend added

Employees answer a tender's personnel criteria: who can be named, what they
hold, and which CV version to attach. They live in an `employees` collection
(set by `MONGO_EMPLOYEES_COLLECTION` in `.env`), created automatically on
first save like the others.

### Experience is two figures, and neither is typed in

A tender asks two different questions — "shall have N years' experience" and
"shall have been a regular employee of the bidder for N years" — and answering
one with the other rules out people who actually qualify. So the form collects
two **dates** and the API derives two figures from them on every read:

| Stored (entered once) | Returned (recalculated on read) | Shown as |
| --- | --- | --- |
| `career_start_date` | `total_experience_years` | **Total Professional Experience** |
| `date_of_joining` | `vgil_experience_years` | **Experience with VGIL** |

Nothing stores a number of years. A typed "7" is right the day it is typed and
wrong every day after, and nobody goes back to revise it; a date stays true and
the years look after themselves. Both figures come back `null` — shown as a
dash, not a zero — when the date behind them is missing, because "not recorded"
and "none" are different answers and only one of them needs chasing up.

`experience_years`, the old single typed number, is no longer the source of
truth for anything: nothing computes from it and no screen shows it. It is
still accepted and stored so that anything already recorded survives a round
trip.

`min_experience` / `max_experience` on the list bound **total** experience,
still in years — the API resolves them to start-date bounds, so the filter goes
on working as the years tick up without anyone re-saving a record. Both figures
are sortable (`total_experience_years`, `vgil_experience_years`); each sorts on
the date behind it, so the ordering is exact even though the figure displayed is
rounded to the "7+ yrs" form the CVs use.

The dates must be ISO (`YYYY-MM-DD`) or the API returns 422. That is stricter
than the free-text fields around them, deliberately: these two are what a
displayed figure is *calculated* from, and a value that silently failed to parse
would show no experience at all rather than a wrong one — the kind of blank
nobody investigates.

Employees also store their company affiliation (`VGIL`, `Fintech`, or
`Unknown`) and a separate `education[]` list; education is not folded into
`highest_qualification`. Imported records may carry a `source_identity` used
for matching and duplicate prevention. It is not editable in the employee
profile form. Migrations 0009–0011 backfill missing employee fields without
replacing existing values, CVs, or certifications; migration 0012 adds a
partial unique index for non-empty source identities.

### Employee certification workbook import

`app.employee_excel_import` reads the `VGIL` and `Fintech` sheets from
`data_import/employee_certifications/CERTIFICATION OF EMPLOYEE.xlsx`. For
VGIL, only Column A is used as the employee ID; the other `Employee id`
column is ignored. Both conflicting VGIL ID 611 rows are always skipped.
Fintech identities use `fintech:<normalized full name>`. AI certifications
and education are joined to source employees only by an unambiguous,
normalized full name; their workbook ID columns are never treated as verified
VGIL identities. If there is no clear primary-workbook match, a new
`Unknown`-affiliation employee uses `unknown:<normalized full name>`. Exact
names repeated between AI and education are consolidated under that identity.
Similar but non-identical names, including possible matches to existing
employees or the excluded ID 611 records, go to the separate Manual Name
Review list instead of being assigned or created automatically. Review rows
include source workbook, sheet, physical row, possible matches, and reason.

Start with a dry run against an isolated local database. No `.env` file is
loaded; the target URI and database are explicit, and the tool refuses remote
hosts or database names without a `_scratch`, `_test`, or `_local` suffix.
`doc_collection_dev` is refused unless the operator explicitly supplies
`--allow-doc-collection-dev`; loopback-host validation and the explicit
identity allowlist still apply:

```
.\venv\Scripts\python.exe -m app.employee_excel_import `
  --mongo-uri "mongodb://127.0.0.1:27184/" `
  --database "pms_employee_certification_pilot_scratch" `
  --source-identity "vgil:34" `
  --source-identity "vgil:301" `
  --source-identity "vgil:995"
```

The report separately lists employees to create, existing employees to update,
skipped records, and Manual Name Review entries. An exact source-identity
match is preferred; otherwise only one exact normalized full-name match may
be updated. Duplicate matches, conflicting company/employee-code values, or
inconsistent source rows are flagged for review and are not written. Updates
add missing education and certification names, fill a missing identity only
when the match is unambiguous, and set a missing or `Unknown` company only
when the workbook verifies the affiliation. Auxiliary-only records do not
change the company or identity of an existing VGIL/Fintech employee. Updates
do not replace existing profile details, CVs, or certifications, and do not
attach files to certification entries. Re-running the same import does not
add duplicate employees, education, or certification names.

Only after reviewing the dry-run report, the same explicitly selected sample
can be applied with `--apply`. Apply mode requires an identity allowlist and
is capped at 10 identities per invocation. Do not point it at a shared or
server database; production import is intentionally outside this tool's
permitted target scope. Apply one invocation at a time. Writes are made
per employee rather than in a multi-document transaction; if an error is
reported, review the listed earlier actions and rerun a dry run before retrying.

The parser uses Python's standard library and does not download certificate
files or follow workbook hyperlinks.

```
GET    /employees/            one page: filter, sort, search, page
POST   /employees/            create                              -> 201
GET    /employees/{id}        one employee, CVs and certifications included
PUT    /employees/{id}        replace the profile fields
DELETE /employees/{id}        delete the employee                 -> 204
GET    /employees/certifications        every certification held by anyone,
                                        flattened, validity computed
GET    /employees/designations          filter vocabulary
GET    /employees/departments           filter vocabulary
GET    /employees/certification-names   filter vocabulary
GET    /employees/issuing-bodies        filter vocabulary
POST   /employees/{id}/cvs                        add a CV version
PUT    /employees/{id}/cvs/{cv_id}                edit one
DELETE /employees/{id}/cvs/{cv_id}                remove one
POST   /employees/{id}/certifications             add a certification
PUT    /employees/{id}/certifications/{cert_id}   edit one
DELETE /employees/{id}/certifications/{cert_id}   remove one
```

Worth knowing:

- **CVs and certifications are embedded in the employee**, not a collection
  of their own — neither means anything apart from its person, and this is
  the shape the screens were always built around. Each nested record has its
  own id and endpoints, so a profile edit can never drop a CV someone
  uploaded meanwhile.
- **Files still go through the document log.** The frontend uploads a CV or
  certificate scan to `/documents/` first (via api/attachments.js) and the
  nested record keeps the `document_id`. Deleting an employee removes the
  records but leaves the files in the log — which is what the confirmation
  dialog promises.
- **Certificate validity is computed, never stored.** The flattened index
  works it out against `today` (sent by the browser) at read time, so
  nothing goes stale at midnight.

Anything saved into the old browser-local store is still in that browser
under `pms.employees.v1`, unread and unmigrated, same as `pms.projects.v1`
before it. Say the word if you want a one-off import written.

## 3d. What the Tendering backend added

Tenders track a bid from identification to outcome. They live in a `tenders`
collection (set by `MONGO_TENDERS_COLLECTION` in `.env`) and combine the two
shapes the earlier areas established: nested records like an employee's, and
document links like a project's.

```
GET    /tenders/             one page: filter, sort, search, page
POST   /tenders/             create                              -> 201
GET    /tenders/{id}         one tender, costs and certificates included
PUT    /tenders/{id}         replace the form fields
DELETE /tenders/{id}         delete the tender                   -> 204
GET    /tenders/authorities  filter vocabulary
GET    /tenders/tags         filter vocabulary
POST   /tenders/{id}/cost-items                   record a cost (EMD, fees)
PUT    /tenders/{id}/cost-items/{item_id}         edit one
DELETE /tenders/{id}/cost-items/{item_id}         remove one
POST   /tenders/{id}/certificates                 record a certificate
PUT    /tenders/{id}/certificates/{cert_id}       edit one
DELETE /tenders/{id}/certificates/{cert_id}       remove one
GET    /tenders/{id}/documents                    that tender's documents
POST   /tenders/{id}/documents/{document_id}      attach a document
DELETE /tenders/{id}/documents/{document_id}      detach a document
GET    /tenders/{id}/projects                     the projects it cites
POST   /tenders/{id}/projects/{project_id}        cite a project as evidence
DELETE /tenders/{id}/projects/{project_id}        stop citing one
```

Worth knowing:

- **The list endpoint carries the whole pipeline view.** The open/all split
  (`open_only`), the multi-status filter, the deadline window, and the
  estimated-value range are all query parameters; a tender with no deadline
  or no value recorded can never fall inside a window or a range. The
  server's OPEN_STATUSES list must stay in step with the frontend's.
- **Receipts and scans still go through the document log.** The frontend
  uploads through `/documents/` first (api/attachments.js) and the cost item
  or certificate keeps the `document_id`. Deleting a tender removes its
  nested records but leaves every file and document in the log — which is
  what the confirmation dialog promises.
- **A cost item's receipt is not one of the tender's documents.** The
  Documents tab holds what the bid was *filed with* — the notice, a
  corrigendum, the submitted bid, an award letter — which are documents that
  can be put to a tender again. A payment receipt is evidence of what this
  one bid cost, so it stays on the Costs & certificates tab with the amount
  and instrument number it belongs to, and carries no `tender_id`. Deleting
  such a document from the log clears the pointer on the cost item or
  certificate (and its stale `file_name`) but keeps the record itself, the
  way deleting a CV's file keeps the CV.

Anything saved into the old browser-local store is still in that browser
under `pms.tenders.v1`, unread and unmigrated, like `pms.projects.v1` and
`pms.employees.v1` before it. Say the word if you want a one-off import
written. With tenders moved, `frontend/src/api/localStore.js` had no
importers left and was deleted, as its own comment always planned.

## 3d-i. The document -> tender link, and migration 0004

The same move projects made in 0001 and employees in 0003, for the third and
last area. Until now the only record of which tender a document was attached
to was the tender's own `document_ids` array, so a document deleted from the
log left its id behind there, pointing at nothing.

A document now also carries `tender_id` (or `null` for a document that is no
tender's), and one endpoint follows from it:

```
GET /tenders/{id}/documents     that tender's documents, newest first
```

which the Documents tab reads instead of downloading the whole log and
filtering it in the browser.

**Attaching is deliberately not exclusive.** A project refuses a document
another project holds, and so does an employee; a tender does not, because
the documents a bid is filed with — a registration certificate, an ISO
certificate, a past work order — are meant to be put to bid after bid. So
`document_ids` may name the same document on several tenders. `tender_id`
can only name one, and keeps the first claim rather than moving; which is
why the endpoint above matches on `tender_id` **or** the tender's
`document_ids`, and why reading only `tender_id` would hide a document from
the second tender that holds it.

Deleting a document clears it from every tender's `document_ids`; deleting a
tender clears `tender_id` from its documents but leaves the documents in the
log.

`0004_document_tender_id` sets `tender_id` from the existing `document_ids`
and creates `ix_documents_tender_id`. Like 0001 it refuses to guess: a
document two tenders both list, a reference pointing at a document that is
not there, a document already holding a different tender's id, and a document
that is already a project's or an employee's are all **reported and left
exactly as they are**. It reads `document_ids` only — a cost item's receipt
is not claimed, because it is not one of the tender's documents.

## 3d-ii. Cited projects, and migration 0005

A tender cites the past work it puts forward as experience, in
`cited_project_ids`:

```
GET    /tenders/{id}/projects                  the projects it cites
POST   /tenders/{id}/projects/{project_id}     cite one
DELETE /tenders/{id}/projects/{project_id}     stop citing one
```

Each row comes back as an ordinary project, carrying the same
`document_types` the projects list resolves, so the evidence strip reads the
same on this tab as it does on the Projects screen.

**A citation is not ownership.** Citing a project changes nothing about it,
and does not reach its documents: `projects.document_ids` and
`documents.project_id` are neither read nor written, and a cited project's
files do not appear on the tender's Documents tab. Citing is not exclusive
either, and never could be — the whole point of past evidence is that the
same completed project is put forward bid after bid. Deleting a project
clears the citation from every tender that made it; the tender itself is
untouched.

This is **not** the project a won tender produced. There is no
`tenders.project_id`, and citing carries no outcome, no assignment and no
status.

`0005_tender_cited_project_ids` gives every existing tender an empty
`cited_project_ids` and creates `ix_tenders_cited_project_ids`. Unlike its
three siblings it derives nothing: no record of which projects a tender cites
has ever existed, so there is nothing to read it from. A citation pointing at
a project that is no longer there is reported rather than cleared.

## 3e. What manual redaction added

Opening any PDF, PNG or JPG in the document preview now offers **Redact**
beside Download. That switches the preview into a selection view: drag one or
more rectangles over the costs, amounts or anything else that must not leave
the building. **Preview redaction** then shows the finished copy before you
commit to it, and **Generate redacted copy** downloads a permanently redacted
*copy* of the file.

Below the page are **−, a zoom readout, + and Reset zoom**, stepping through
50% to 400%. Zooming scales the drawing surface itself and changes nothing
about how an area is recorded — rectangles are stored as fractions of that
surface's measured box, so the same drag describes the same part of the page
at any zoom. What it buys is precision: one pixel of mouse movement is 0.69pt
of page at 100% and 0.17pt at 400%, which is the difference between being able
to box a single figure in a dense cost table and not. A zoomed page scrolls in
its frame, and a middle-button drag pans it.

Preview is not a mock-up: it asks the backend for the real copy and displays
those bytes, in the same viewer the rest of the app uses. For a PDF that is
the browser's own reader, so the redaction can be checked the way a recipient
would check it — by trying to select the text that used to be there. The
generated bytes are cached until the selection changes, so previewing and then
downloading produces one file, not two, and what was checked is what is saved.

Three things this deliberately does not do:

- **It never touches the original.** No endpoint below writes to
  `backend/app/uploads/`, changes a document record, or replaces a stored
  file. The original keeps previewing and downloading exactly as before, with
  its text intact — the verification suite hashes it before and after to prove
  it.
- **It does not save the copy.** The copy is generated per request and
  streamed straight to the browser as a download. There is no second file on
  the server and no second row in the document log, so search, filters and
  duplicate detection see nothing new.
- **It is not a box drawn on top.** MuPDF's redaction pass *deletes* the
  covered text from the page's content stream and blanks the covered pixels of
  any image, then fills the area solid white. There is nothing left underneath
  to select, copy, or pull out with a text extractor — the fill is what
  remains after the content is gone, not something laid over it. Automatic
  redaction — finding the amounts for you — is not part of this stage.

  A white fill reads as blanked-out paper rather than as a struck-through
  passage, so on a white page a redacted area is not obvious to whoever
  receives the copy. That is what a white-out redaction is; if the copies ever
  need to *announce* that something was removed, the fill is one constant
  (`PDF_FILL` in `app/redaction.py`). On the selection screen the marks carry a
  hairline outline so they stay visible against the page — that outline is part
  of the UI only and is not in the generated file.

```
GET  /documents/{id}/redaction/source            page count and page sizes
GET  /documents/{id}/redaction/pages/{n}?width=  one page rendered as a PNG
POST /documents/{id}/redacted-copy               the copy, as a download
```

The page-image endpoint exists because the ordinary preview shows a PDF in an
`<iframe>`, and an iframe is opaque: nothing outside it can tell where on the
page a click landed. Redaction draws over a rendered page instead, which also
means PDFs and images are selected in exactly the same way. Rectangles are
sent normalised — x, y, width and height as fractions of the page, origin
top-left — so the browser can render the page at any size and still describe
the same region.

This added one dependency, **PyMuPDF**, so an existing checkout needs:

```
cd backend
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

It is imported lazily, so an install that skips this still starts and runs
normally — only the three endpoints above fail, with a message saying to
install it. Note that PyMuPDF is **AGPL-3.0** (its authors sell a commercial
licence separately). That is fine for in-house use; it is worth revisiting
before the PMS is distributed to anyone outside.

`verify_mongo.py` covers all of the above, including that the selected text is
gone from the copy's decompressed page content and that the stored original is
byte-for-byte unchanged.

## 3e-i. Saving the redacted copy to the log, and migration 0006

The redaction view has a second action beside **Generate redacted copy**:
**Save to document log**. Generate still downloads the copy and leaves nothing
on the server. Save files it:

```
POST /documents/{id}/redacted-copy/save    log the copy as its own entry
```

It returns the new entry, which is an ordinary document in every respect --
listed, searchable, previewable and downloadable through the same endpoints as
any other. Three things identify it:

* its file name ends `_redacted` (`cost sheet.pdf` -> `cost sheet_redacted.pdf`),
* its notes open with `Redacted copy of <the original's file name>.`, and
* `redacted_from_id` holds the original document's id. The Documents list
  shows a **Redacted copy** chip for an entry that carries it.

**The original is never written to.** Its stored file is opened for reading to
generate the copy and nothing else: no `update_one` names it, no storage call
touches it, and it keeps its own file, hash, metadata, owner and text. The copy
is a *new* file under a new UUID and a *new* row. The link points one way only
-- from the copy back to the original -- which is what keeps saving a copy from
being a write to the original in any form. Afterwards the original previews,
downloads and hashes exactly as it did before.

The copy is deliberately **not** attached to the original's project, employee
or tender. Attaching would mean writing to that owner's own `document_ids`, and
would put a second file into an evidence tab nobody asked to change. The copy
lands in the log; attaching it anywhere is a decision someone makes, through
**Attach existing** like any other document.

Order of operations, the same one every upload in `documents_mongo.py` uses:
generate, hash, check for a duplicate, write the file, insert the row. A
rejected copy leaves nothing on disk; an insert that fails takes the file it
wrote back out. There is no state in which a file exists with no entry for it,
and the UI reports a save only when the entry comes back.

### The migration

`0006_document_redacted_from_id` gives every existing document the field, set
to None, and creates `ix_documents_redacted_from_id`. Like 0005 it derives
nothing: redaction used to keep no trace of a copy, so there is no record to
read provenance from. An entry whose file name merely *ends* in `_redacted` is
reported and left alone -- a name is not provenance.

**The application does not require it.** A document with no `redacted_from_id`
reads as "not a redacted copy" either way, so the feature works on an
un-migrated database. Run it to keep every document carrying the field
explicitly, as 0001, 0003 and 0004 did for the ownership fields.

The local and server databases are separate, so it has to be run against each
one. From the `backend` folder, with `.env` pointing at the database you mean
to change:

```
.\venv\Scripts\python.exe migrate.py --status     # 0006 shows as pending
.\venv\Scripts\python.exe migrate.py --dry-run    # report only, writes nothing
.\venv\Scripts\python.exe migrate.py              # apply it
```

It works in place, touches only the `documents` collection plus its own record
in `migrations`, and a second run writes nothing.

## 3f. What automatic detection added

Opening the redactor now also **scans the document for financial figures** —
contract values, prices, rates, fees, totals, EMD, quoted amounts — and offers
them as *suggested* areas. They are proposals only: a dashed amber box covers
nothing until you accept it, and an accepted one becomes an ordinary redaction
area and goes through the same **Generate redacted copy** as a hand-drawn one.
Reject leaves it alone. You can still draw boxes by hand at any point, and the
original file is never touched — the scan endpoint only reads.

The review panel above the page lists what was found, with **Accept** /
**Reject** per figure and **Accept all** / **Reject all**. A suggestion marked
*unsure* came from the one rule that does not claim certainty (a grouped number
with no label or currency symbol near it).

### OCR (needed for scans and images)

Detection reads each **page** the best way it can:

| Document | How it is read |
| --- | --- |
| Normal text PDF | the PDF's own text layer |
| Scanned PDF | OCR |
| Mixed PDF | per page — text layer where there is one, OCR where there is not |
| JPG / PNG | OCR |

**OCR needs nothing installed.** `pip install -r requirements.txt` is the whole
setup, on Windows and on Linux alike.

That is not the usual arrangement with Tesseract, so it is worth stating why:
PyMuPDF's wheels carry **Tesseract and Leptonica linked into MuPDF's own shared
library**, so OCR runs inside the API process. There is no Tesseract executable
to install, nothing needs to be on `PATH`, and no global `TESSDATA_PREFIX` is
set — the backend passes an explicit path on every call, which PyMuPDF uses
verbatim without touching the environment.

The only thing OCR needs from outside the process is the language data, and the
project ships its own copy:

```
backend/ocr/tessdata/eng.traineddata
```

**This is what makes the PMS deployable to a shared server.** No root access is
required, nothing is installed system-wide, and other projects on the same
machine are neither affected nor depended on — the backend prefers its own copy
even on a machine that has a Tesseract of its own, so a system upgrade
elsewhere cannot change what this app reads.

To point somewhere else — a shared volume, or an extra language — set
`PMS_TESSDATA_DIR` in `backend/.env` to a folder holding the `.traineddata`
files. Deliberately not `TESSDATA_PREFIX`, which is global to every Tesseract
on the machine. The lookup order is: `PMS_TESSDATA_DIR`, the project's own
copy, `TESSDATA_PREFIX`, then any system install.

**If the language data is missing the app still runs and the text-PDF path
still works.** Scanned pages and images are then reported as unread, with a
message saying so — deliberately, because a scan that came back with no
suggestions would otherwise look exactly like a page with nothing sensitive on
it. To check what a running backend resolved, from the backend folder:

```powershell
.\venv\Scripts\python.exe -c "from app import detection; print(detection.tessdata_dir())"
```

On the server, the same thing with that machine's interpreter. It should print
the path to `backend/ocr/tessdata`.

A suggestion read by OCR is marked **OCR** in the list and drawn with a dotted
outline rather than a dashed one, because OCR is a guess about pixels in a way
reading a content stream is not — those are the ones worth checking against
the page before accepting. OCR runs at 300 DPI (about 0.4s per page here), and
one pass OCRs at most 40 pages; any beyond that are reported as not scanned
rather than left to hang the request.

`verify_mongo.py` covers every format above — text PDF, scanned PDF, mixed PDF,
JPG, PNG and rotated pages — checking that the figures are found, that the
suggested boxes sit on actual ink, and that accepting them blanks exactly those
pixels and nothing else.

### Diagnosing a missed figure

`backend/diagnose_detection.py` opens a PDF read-only and prints what the
detector read and what it made of it, which answers "why was that amount not
suggested?" (the text never arrived, no rule matched it, or a box was dropped).
Usage is in the file's own header.

## 3g. Company Documents

The company's own certificates, registrations and financial statements. They
are rows of the shared `documents` collection marked `scope: "company"`, so
storage, hashing, duplicate refusal, preview, download and delete are the
document log's own; they are kept apart from employee CVs and certifications,
which are different records with their own owner.

- **Fields:** title, document type, category, subcategory, issuing
  organisation, certificate number, issue date, expiry date, notes. Only the
  title and the file are required; a field the source does not support stays
  empty rather than being guessed.
- **Status** is computed on every read from the expiry date and never stored:
  *Expired* (date passed), *Expiring soon* (within 90 days), *Valid*, or
  *Expiry not recorded* when there is no expiry date. A date printed on a
  certificate that does not state an expiry should not be entered as one.
- **Endpoints:** `GET/POST /company-documents/`, `GET /company-documents/facets`,
  `GET/PUT /company-documents/{id}`. The file itself is served by
  `GET /documents/{id}/file`, and a delete is `DELETE /documents/{id}`.
- **Access:** Admin and HOD may list, view, add and edit; only Admin may
  replace the stored file. Normal Users have no access.
- **Migrations:** `0013_company_documents` creates the `scope`/`category`
  index and a unique `source_key` index (the stable import identity).

### Bulk import (operator tools)

No data is stored in this repository. To load a batch of company documents:

1. **Build a package** on the machine that holds the originals:
   `python -I scripts/build_company_package.py --source DIR --metadata FILE
   --ocr-cache DIR --out DIR`. The metadata file is a JSON list; each entry
   names a file by `path` (relative to `--source`) and carries only values
   verified from the document (`title`, `document_type`, `category`,
   `subcategory`, `issuing_organisation`, `certificate_number`, `issue_date`,
   `expiry_date`, `notes`). The package is a `manifest.json` plus the files,
   keyed by SHA-256, with any extracted text attached.
2. **Take a backup**, then from `backend/`, with the migrations applied:
   `python import_company_documents.py PACKAGE --confirm-db NAME --dry-run`
   and, when the dry run looks right, the same without `--dry-run`.
   `--confirm-db` must equal the database in use, so a script pointed at the
   wrong environment stops instead of writing.

The import is safe to repeat. Per file it reports *inserted* (new),
*updated* (an already-imported record had an empty field the package can
fill; filled fields are never overwritten and a record a person has saved is
never touched), *skipped* (nothing to add, or the same file is already held by
a non-company record, which is left as it is) or *failed* (missing file, hash
mismatch, unsupported type or over the upload limit). Nothing is deleted. A
report is written to `PACKAGE/reports/`.

### Optional OCR helper

Scanned certificates have no text layer. `scripts/llmthing_ocr.py` sends
their pages to an LLMThing vision model and caches the transcription by file
hash, so a file is never sent twice; pages that already have text are read
directly. It needs a token in a file whose path is in the
`LLMTHING_TOKEN_FILE` environment variable (never printed or stored), and
optionally `LLMTHING_BASE_URL` and `LLMTHING_MODEL`. It is an operator tool run
on a workstation; the server never needs it.

It is resumable (pages are cached as they are read, so a run that stops
part-way continues from the next page) and it never falls back to another OCR
service: a page LLMThing cannot read marks that file as failed in `--log FILE`
(one JSON line per file) and in the exit status, and the run carries on with
the next file. `--max-pages N` reads only the first N pages of each file.

OCR text is good for describing a document but can misread **identifiers and
dates** — reference numbers, certificate numbers, issue and closing dates. Put
such a value in the metadata only after checking it against the page image.

## 3h. Proposal Library

Reusable proposal write-ups — today the CBS feature write-ups — that belong to
no tender. Each record is one requirement from a source RFP (identified by its
section number, section title, optional subsection and point number) together
with the answer written for it. They have their own `proposal_library`
collection rather than the document log, which is organised around tenders,
projects and employees.

- **What is stored:** the original `.docx` or `.rtf` (kept for download) and
  its extracted text, split on the pattern every sampled file follows: the
  first paragraph (with any bullet items directly under it) is the requirement
  and the rest is the response. The split
  is a heuristic, so both texts can be corrected in the edit form. Pictures in
  a write-up are counted but not shown in the app; download the original to see
  them. RTF files are read by a structural parser (style tables, headers and picture
  data are skipped); text formatting is not kept.
- **Screen:** a **View** button opens the original in a read-only viewer
  (paragraphs, bold/italic, lists, tables and pictures, with the page header
  and footer shown above and below as in the downloaded file; page layout is
  not reproduced, and an `.rtf` shows text only) before anything is downloaded; search
  over requirement and response text, filters by part
  (first number of the section), section, file type and pictures, sort by
  section number (numerically: 3.2 before 3.10), a reading drawer with a
  **Copy response** button, and add / edit / delete.
- **Endpoints:** `GET/POST /proposal-library/`, `GET /proposal-library/sections`,
  `GET/PUT/DELETE /proposal-library/{id}`, `GET /proposal-library/{id}/file`
  (download) and `GET /proposal-library/{id}/preview` (the original as read-only
  HTML for the in-app viewer).
  Uploads accept `.docx` and `.rtf` only, up to the same 20 MB limit; the
  document log's PDF/PNG/JPG-only rule is unchanged.
- **Access:** Admin and HOD. Normal Users have no access.
- **Migration:** `0014_proposal_library` creates the indexes (section order, a
  unique `source_key`, file type). Nothing creates them at startup, so apply it
  before importing.

### Bulk import

`python import_proposal_library.py SOURCE_DIR --confirm-db NAME [--dry-run] [--limit N]`,
from `backend/`. `SOURCE_DIR` is a folder whose sub-folders are the sections
(for example `3.3.7 Loan account management`); the section number and title
are read from the folder name, a deeper folder becomes the subsection, and a
trailing number in the file name becomes the point number. Per file it reports
*inserted*, *skipped* (the same bytes are already in the library) or *failed*
(the file is not a valid `.docx`/`.rtf`); other file types are ignored and
counted. `--limit N` takes only the first N files (folder path order, then file
name) for a pilot batch; it also lists metadata the files did not supply
(left empty, never guessed). It is safe to repeat, deletes nothing, and writes
a report to an `import_reports/` folder next to `SOURCE_DIR`. Take a backup
first.

## 3i. Importing tender documents

`backend/import_tender_documents.py` loads tenders together with the documents
that belong to them, into the structure the Tenders screens already use. No
data is stored in this repository.

`python import_tender_documents.py PACKAGE_DIR --confirm-db NAME [--dry-run]`,
from `backend/`. `PACKAGE_DIR` holds a `manifest.json` and the files it names
(keep the original file names). The manifest lists tenders; each has `fields`
(title, issuing authority, reference number, type, published date, submission
deadline, submission mode, fee, value, EMD, status, notes, tags) and its
`documents` (file, original name, SHA-256, document type, category, reference
number, document date, notes). Put in only what the documents state: anything
left out is stored empty, and the tender fields are validated by the same
rules as the tender form.

To build the package from a folder of bids use `scripts/build_tender_package.py`
(`--source ROOT --bids FILE --ocr-cache DIR [--ocr-log FILE] --out DIR`). The
curated `--bids` list gives, per bid folder, the tender fields and, per file,
its `kind` (`client`, `vgil` or `other`), `document_type` and a one-line
`what`; the builder hashes and copies each file, attaches OCR text for scanned
ones, and writes `processing_log.json` saying, per file, whether it was
packaged and anything incomplete about it (a scan with no OCR result, over the
size limit, a type the store does not accept, image-only pages, no reference or
date). A file in a bid folder that the list does not mention is logged as
`unclassified` and left out.

The application's upload rules (PDF/PNG/JPG, 20 MB) apply to this importer too.
A bulk load of bid files that legitimately break them is opt-in per run:
`--max-mb N` raises the size limit and `--allow-ext docx,xlsx` accepts Word and
Excel files. Word and Excel records download normally; the app shows "No
preview for this file type" for them.

Keep the two kinds of document apart by type: what the client issued (for
example *Tender Document*) is not what VGIL submitted (*Submitted Bid*,
*Technical Proposal*, *Financial Proposal*), and the notes should say which.

Per tender it reports *created* or *existing* (matched on issuing authority
plus reference number; an existing tender is never changed). Per document it
reports *inserted*, *linked* (the file was already in the log and free of any
other owner), *skipped* (already attached to this tender, or owned by
something else, which is left as it is) or *failed* (missing file, hash
mismatch, unsupported type, over the 20 MB upload limit). It is safe to repeat,
deletes nothing, and writes a report to `PACKAGE_DIR/reports/`. Files the
document log does not accept (Word, Excel) or above the limit cannot be
imported by it. Take a backup first.

## Migrations at a glance

Run from `backend/`: `python migrate.py --status`, `--dry-run`, then
`python migrate.py` (`--force` re-runs one that is already recorded). Each
migration records itself, so running the runner again does nothing. They change
only the collections they name.

| Migration | What it does |
|---|---|
| 0001 `document_project_id` | Populate `documents.project_id` from each project's document list, and index it. |
| 0002 `standardise_contract_type` | Rename document type "Contract (Consultant Services)" to "Contract". |
| 0003 `document_employee_id` | Populate `documents.employee_id` from employees' CVs and certifications, and index it. |
| 0004 `document_tender_id` | Populate `documents.tender_id` from tenders' document lists, and index it. |
| 0005 `tender_cited_project_ids` | Add `cited_project_ids` to every tender, and index it. |
| 0006 `document_redacted_from_id` | Add `redacted_from_id` to every document, and index it. |
| 0007 `login_throttle_indexes` | TTL index on the login rate-limit windows. |
| 0008 `auth_indexes` | Unique username, unique session token hash, session expiry TTL. |
| 0009 `employee_company` | Backfill missing employee company affiliations as "Unknown". |
| 0010 `employee_education` | Initialise missing employee education arrays. |
| 0011 `employee_source_identity` | Initialise missing employee source identities as null. |
| 0012 `employee_source_identity_index` | Unique partial index on employee `source_identity`. |
| 0013 `company_documents` | Indexes for company documents (`scope`/`category`, unique `source_key`). |
| 0014 `proposal_library` | Indexes for the `proposal_library` collection. |
| 0015 `remove_review_flags` | Remove the retired `needs_review` and `expiry_unverified` fields from company documents and the library. |

Sections 3b–3f above describe the earlier ones in more detail.

## 4. PDF splitter (optional, separate from the app)

`scripts/pdf_splitter/` is a standalone tool for cutting a large combined PDF
into individual documents, using an Excel index of page ranges and metadata.
It has its own virtual environment and dependencies, and neither imports from
nor is imported by the backend — nothing here affects the running app. Setup
and usage are in `scripts/pdf_splitter/README.md`.

## Notes

- Both servers need to stay running in their own terminals while you use
  the app. `Ctrl+C` in either terminal stops that server.
- If the frontend shows a network error when saving, double check the
  backend terminal is still running and reachable at port 8000.
- Uploaded files live on disk in `backend/app/uploads/`, named by UUID;
  MongoDB stores only the metadata and the pointer. Back the folder up together
  with the database.
- For deployment to a Linux server, see "Deploying to a Linux server" in
  `README.md`.

# Procurement Management System (PMS)

An internal tool that keeps the projects, people, documents and tenders a bid
is assembled from in one place, so the evidence a tender asks for is ready to
pull from instead of being chased down each time.

- **Frontend:** React 19 + Vite (`frontend/`)
- **Backend:** FastAPI + pymongo (`backend/`)
- **Database:** MongoDB
- Runs entirely on your own infrastructure; no cloud service is required.

The repository contains **code only**. Real records, uploaded documents,
source files for imports and database backups are never committed (see
[What is not in the repository](#what-is-not-in-the-repository)).

For the long-form reference (how each area works, the redaction and OCR
tools, the migrations, the import tools) see [`SETUP.md`](SETUP.md).

## What it does

| Area | Purpose |
|---|---|
| Projects | Past projects a tender cites as evidence, with their documents. |
| CVs (employees) | People, CV versions and certifications. |
| Tenders | Tenders, cost items, submitted certificates, evaluation criteria and the documents each tender holds (notice, proposals, forms, award). |
| Documents | The shared document log: every uploaded file, with preview, download, manual redaction and automatic sensitive-figure detection (OCR). |
| Company Documents | The company's own certificates, registrations and financial statements, with issuer, number, issue/expiry dates and a Valid / Expiring soon / Expired status computed from the expiry date. Kept separate from employee CVs and certifications. |
| Proposal Library | Reusable proposal write-ups (requirement + answer, by RFP section number) that belong to no tender, searchable by text. |
| User management | Accounts and roles (Admin only). |

### Roles

| Role | Access |
|---|---|
| Admin | Everything. Only Admins use Projects, CVs, Tenders and User management, and only Admins can replace a stored file. |
| HOD | Documents, Company Documents and Proposal Library: list, view, download, add and edit. Cannot replace files; can delete only documents nothing refers to. |
| Normal User | Can upload documents only. Cannot list, view, download, edit or delete. |

The API enforces these rules itself; hiding a menu item is not the control.

## Prerequisites

- **Python 3.11** (the backend was developed and tested on 3.11.9).
- **Node.js and npm** — a current LTS release. Developed and tested on Node 24.
- **MongoDB** reachable from the backend. Developed against MongoDB
  Community Server 8.x with pymongo 4.10. No MongoDB is needed to run the
  automated tests.
- No system OCR package is needed: PyMuPDF bundles Tesseract, and the English
  language data ships in `backend/ocr/tessdata/`.

## Quick start (local development)

### 1. Backend

From the `backend` folder.

Windows (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Linux / macOS:

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

An unchanged `.env` works against a default local MongoDB (`localhost:27017`,
no authentication, database `doc_collection_dev`). The database and its
collections are created on first use.

Apply the database migrations, then create the first administrator:

```
python migrate.py --status      # which migrations are pending
python migrate.py --dry-run     # what they would change, writing nothing
python migrate.py               # apply the pending ones, in order
python -m app.create_admin      # prompts for a username and password
```

`create_admin` refuses to run unless MongoDB is local and the database name
ends in `_dev`, `_local` or `_test` (see [Deploying to a Linux
server](#deploying-to-a-linux-server) for the production case).

Start the API:

```
uvicorn app.main:app --reload --port 8000        # any OS, venv active
.\run.ps1                                         # Windows: uses the venv directly
```

Check `http://127.0.0.1:8000/health` and the interactive API docs at
`http://127.0.0.1:8000/docs`. The API still starts if MongoDB is down; requests
that need it return a 503 saying what to fix.

### 2. Frontend

From the `frontend` folder:

```
npm install
npm run dev
```

Open the address Vite prints (normally `http://localhost:5173`) and sign in
with the administrator you created. The frontend calls
`http://localhost:8000` unless you set `VITE_API_BASE_URL` (copy
`frontend/.env.example` to `frontend/.env.local`). In development the API
accepts logins from `localhost` / `127.0.0.1` on ports 5170–5179.

## Configuration

Real `.env` files are git-ignored; the committed `.env.example` files list
every variable the code reads, with defaults and comments.

**Backend — `backend/.env`** (full list in `backend/.env.example`)

| Variable | Needed? | Purpose |
|---|---|---|
| `MONGO_HOST`, `MONGO_PORT` | Optional locally (`localhost`, `27017`); **set in production** | Where MongoDB is. |
| `MONGO_USER`, `MONGO_PASSWORD`, `MONGO_AUTH_SOURCE` | **Set when MongoDB authentication is on** | Credentials. Leave empty for an unauthenticated local server. |
| `MONGO_URI` | Optional | Replaces host/port/credentials (replica set, Atlas). |
| `MONGO_DB_NAME` | **Set in production** (default `doc_collection_dev`) | Which database to use. |
| `MONGO_*_COLLECTION` | Optional | Collection names (documents, projects, employees, tenders, proposal library, users, sessions, login throttle). Defaults are fine. |
| `PMS_COOKIE_SECURE` | **Must be `true` on any HTTPS deployment**; `false` only for local HTTP | Session cookie `Secure` flag. |
| `PMS_COOKIE_SAMESITE`, `PMS_SESSION_HOURS` | Optional (`strict`, `8`) | Session cookie behaviour and lifetime. |
| `PMS_ALLOWED_ORIGINS` | Only if the frontend is on a different origin from the API | Comma-separated HTTPS origins allowed to log in. |
| `PMS_RATE_LIMIT_SECRET` | **Required when running more than one worker/instance** | HMAC key for the login-throttle records; must be identical everywhere. |
| `PMS_LOGIN_*` | Optional | Login rate-limit tuning. |
| `PMS_TESSDATA_DIR` | Optional | Use OCR language data from somewhere other than `backend/ocr/tessdata/`. |

**Frontend — `frontend/.env.local`**

| Variable | Needed? | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | Optional in development (defaults to `http://localhost:8000`); **set deliberately for a production build** | Where the API is. Baked in at build time. |

Never put real credentials in an `.env.example` file or anywhere else that is
committed.

## Tests, checks and build

Backend (from `backend`, venv active; no MongoDB needed — the tests use
mongomock and a temporary uploads folder):

```
pip install -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```

The `verify_*.py` scripts (`verify_mongo.py --offline`, `verify_projects.py`,
`verify_employees.py`, `verify_tenders.py`) are older end-to-end checks that can
run against an in-memory database with `--offline`; they write and remove
temporary files in the real uploads folder, so prefer the unittest suite.
No Python linter is configured.

Frontend (from `frontend`; no backend needed):

```
npm test           # node:test suites in frontend/tests/
npm run lint       # oxlint
npm run build      # production build into frontend/dist/
```

## Repository layout

```
backend/
  app/                FastAPI application
    main.py             entry point; mounts the routers, startup index setup
    auth.py             login, sessions, CSRF, roles, user accounts
    rate_limit.py       login rate limiting
    create_admin.py     bootstrap of the first Admin account
    mongodb.py          connection, collection handles, index setup
    storage.py          uploaded-file validation and storage (shared by all routers)
    schemas.py          request/response models and validation
    redaction.py        manual PDF/image redaction
    detection.py        automatic sensitive-figure detection (OCR)
    docx_text.py        text extraction for .docx/.rtf write-ups
    proposal_library.py builds a Proposal Library record from a file
    employee_*.py       employee workbook import and identity helpers
    routers/            one module per area (documents, company documents,
                        proposal library, projects, employees, tenders)
    uploads/            uploaded files, named by UUID (contents git-ignored)
  migrations/         numbered one-off migrations (m0001 ... m0015), run by migrate.py
  tests/              unittest suites (mongomock)
  ocr/tessdata/       OCR language data
  migrate.py          migration runner (--status, --dry-run, --force)
  import_company_documents.py, import_proposal_library.py, import_tender_documents.py
                      bulk-import tools for operators (see SETUP.md)
  verify_*.py         older end-to-end checks
  diagnose_detection.py  shows why the detector did or did not suggest a figure
  run.ps1             Windows launcher that uses the venv's Python
frontend/
  src/app/            shell, sidebar, routes, auth context
  src/components/     shared UI (table, drawer, modal, form fields, previews)
  src/features/       one folder per area
  src/api/            one client module per resource (http.js: base URL, CSRF, errors)
  tests/              node:test suites
scripts/              standalone operator tools, not part of the app
  llmthing_ocr.py       optional OCR helper for scanned certificates (see SETUP.md)
  build_company_package.py  builds an import package for company documents
  build_tender_package.py   builds an import package of tender bids and their documents
  google_drive_readonly.py  read-only Drive metadata listing
  pdf_splitter/         splits a combined PDF using an Excel index
SETUP.md              detailed reference
```

## What is not in the repository

Git ignores, and the repository must never contain: `.env` files, tokens and
credentials, `backend/app/uploads/*` (the uploaded documents), `data_import/`
and any source spreadsheets or archives, import packages and reports, database
dumps/backups (`*.bson`, `dump/`, `backups/`), virtual environments,
`node_modules/`, `dist/` and caches. Data is moved between machines separately
from the code (see below).

## Deploying to a Linux server

> Nothing below has been run on a server yet. It is derived from how the code
> works, with example configuration to adapt and test.

**What the server needs**

- Python 3.11 with `venv`, and enough disk for uploaded documents.
- A MongoDB the backend can reach, ideally with authentication on and a
  dedicated user with `readWrite` on the PMS database.
- A reverse proxy (for example nginx) with a TLS certificate. Node.js is only
  needed to build the frontend; the build can be done elsewhere and
  `frontend/dist/` copied over.
- A service manager (for example systemd) to keep the backend running.

**Steps**

1. **Get the code** onto the server (clone or copy) under a dedicated,
   unprivileged user.
2. **Backend environment:** `python3.11 -m venv venv`, then
   `pip install -r requirements.txt` from `backend/`. No system OCR package is
   needed.
3. **Configure `backend/.env`** from `.env.example`: MongoDB host, credentials
   and `MONGO_DB_NAME`; `PMS_COOKIE_SECURE=true`; a `PMS_RATE_LIMIT_SECRET`
   (`python -c "import secrets; print(secrets.token_hex(32))"`), the same on
   every worker; `PMS_ALLOWED_ORIGINS` only if the frontend is on another
   origin. Keep the file readable only by the service user.
4. **Uploads folder:** the API stores uploaded files in `backend/app/uploads/`
   (the path is fixed in `storage.py`). Make that directory persistent and
   writable by the service user — for example a symlink to a data volume — and
   **back it up together with the database**: records point at files by their
   stored name.
5. **Database:** run `python migrate.py --status`, then `--dry-run`, then
   `python migrate.py`. They were verified on populated local databases and on an
   in-memory empty one, not yet on an empty real MongoDB — read the dry-run
   first. Create the first administrator with `python -m app.create_admin`;
   on a server database it needs `--allow-remote` and a typed confirmation of
   the database name, because it refuses by default anything that is not a
   local database ending `_dev`, `_local` or `_test`.
6. **Run the API** behind the proxy, bound to localhost, for example:

   ```ini
   # /etc/systemd/system/pms-backend.service  (example)
   [Service]
   User=pms
   WorkingDirectory=/opt/pms/backend
   ExecStart=/opt/pms/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
   Restart=on-failure
   ```

   Use more than one worker only with `PMS_RATE_LIMIT_SECRET` set.
7. **Build the frontend** with the API under a prefix, because several API
   routes (`/documents`, `/projects`, `/tenders`, ...) have the same path as a
   page in the app:

   ```
   cd frontend
   npm ci
   VITE_API_BASE_URL=/api npm run build
   ```

8. **Proxy** (nginx example): serve `frontend/dist/` as static files with
   `try_files $uri /index.html;`, and forward `/api/` to the backend with the
   prefix removed. The proxy must pass the original `Host` header (login checks
   that the request's `Origin` matches its `Host`) and allow uploads above the
   application's 20 MB file limit:

   ```nginx
   location /api/ {
       proxy_pass http://127.0.0.1:8000/;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-Proto $scheme;
       client_max_body_size 25m;
   }
   ```

9. **Check** `https://<host>/api/health`, then sign in.

**Data.** The repository holds no data. Existing records and uploaded files are
transferred separately (a MongoDB dump/restore plus a copy of the uploads
folder, or the import tools in `backend/` and `scripts/`, which are documented
in `SETUP.md`; the company-document and proposal-library importers refuse to
run unless told the name of the database they are about to write to). Take a
database and uploads backup before importing anything.

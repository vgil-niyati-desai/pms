# Project Document Collection System

Internal tool for logging LOI, Work Order, and Completion Certificate
records so this evidence is ready to pull from for future tender
submissions, instead of manually chasing people down each time.

Stack: React (Vite) frontend, FastAPI backend, MongoDB database (via
pymongo). Runs entirely on internal/local infrastructure — no external or
cloud services involved.

Current status: MongoDB is the only database backend. Document metadata
and file uploads are submitted from the React form, saved via the FastAPI
backend, and stored in MongoDB.

All three areas now have real backends: `/projects`, `/employees` and
`/tenders` endpoints persisting to their own collections in the same
database. The browser-local stores those screens used while there was no
endpoint are gone — nothing in the frontend touches localStorage any more.

## Folder structure

```
procurement-management-system/
├── backend/         FastAPI app (Python)
│   ├── app/
│   │   ├── main.py          entry point; mounts the routers
│   │   ├── mongodb.py       MongoDB connection + index setup
│   │   ├── storage.py       uploaded-file handling, shared by every router
│   │   ├── schemas.py       request/response validation
│   │   ├── redaction.py     manual PDF redaction
│   │   ├── uploads/         uploaded files, named by UUID
│   │   └── routers/
│   │       ├── documents_mongo.py  document endpoints
│   │       ├── projects_mongo.py   project endpoints
│   │       ├── employees_mongo.py  employee/CV endpoints
│   │       └── tenders_mongo.py    tender endpoints
│   ├── verify_mongo.py      end-to-end check of the documents endpoints
│   ├── verify_projects.py   end-to-end check of the projects endpoints
│   ├── verify_employees.py  end-to-end check of the employee/CV endpoints
│   ├── verify_tenders.py    end-to-end check of the tender endpoints
│   ├── requirements.txt
│   ├── requirements-dev.txt only needed for verify_mongo.py --offline
│   └── .env.example         copy to .env and adjust
├── frontend/        React app (Vite)
│   └── src/
│       ├── app/          shell, sidebar, routes
│       ├── components/   shared UI (table, drawer, modal, fields)
│       ├── features/     one folder per area: projects, cvs, tenders, documents
│       ├── api/          one module per resource; http.js holds the shared
│       │                 base URL and error handling
│       └── index.css     the whole visual system, driven by tokens in :root
└── scripts/         standalone operator tools (not part of the app)
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

MongoDB is not installed on this machine yet. Install the free MongoDB
Community Server:

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

The backend deliberately still starts when MongoDB is unreachable — it
prints a warning, `http://127.0.0.1:8000/docs` still loads, and document
requests return a `503` explaining what to fix, rather than the whole app
refusing to boot.

## 1c. Start the backend

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

This starts the React app, usually at `http://localhost:5173`. Open that
in a browser.

## 3. Test the full flow

1. With both servers running, fill in the form (Document type, Category,
   Client name, and Submitted by are required).
2. Click **Save entry**.
3. It should appear immediately in the **All Entries** table below the
   form — that confirms the round trip worked (React → FastAPI →
   MongoDB → back to React).
4. To confirm directly in the database itself, open MongoDB Compass and
   look at `doc_collection_dev` → `documents`, or from a terminal:
   ```
   mongosh
   use doc_collection_dev
   db.documents.find().pretty()
   ```
   You should see your test document.
5. `http://127.0.0.1:8000/health` reports which backend is active and
   whether it is currently connected — the quickest way to tell a database
   problem apart from a frontend one.

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

## 3c. What the Employees/CV backend added

Employees answer a tender's personnel criteria: who can be named, what they
hold, and which CV version to attach. They live in an `employees` collection
(set by `MONGO_EMPLOYEES_COLLECTION` in `.env`), created automatically on
first save like the others.

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
POST   /tenders/{id}/documents/{document_id}      attach a document
DELETE /tenders/{id}/documents/{document_id}      detach a document
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

Anything saved into the old browser-local store is still in that browser
under `pms.tenders.v1`, unread and unmigrated, like `pms.projects.v1` and
`pms.employees.v1` before it. Say the word if you want a one-off import
written. With tenders moved, `frontend/src/api/localStore.js` had no
importers left and was deleted, as its own comment always planned.

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
- Authentication and the personnel/CV tracking (deferred per Vansh) are
  not part of this stage.
- Uploaded files live on disk in `backend/app/uploads/`, named by UUID;
  MongoDB stores only the metadata and the pointer.

## What changed in the move to MongoDB

- `documents` is now a MongoDB collection instead of a SQL table. There is
  no schema to create and no migration to run.
- Entry ids are now MongoDB ObjectIds, sent to the frontend as their
  24-character hex string (e.g. `68ae1f...`) instead of `1`, `2`, `3`. The
  React app only compares ids and puts them in URLs, so no frontend file
  changed.
- The search box still does a case-insensitive substring match over client
  name, project title, and reference number — `ILIKE '%q%'` became a
  case-insensitive regex, with the search text escaped so that characters
  like `(` or `*` are matched literally.
- Duplicate-file rejection still works the same way, and is now backed by a
  unique index on `file_hash` so two entries cannot hold the same file even
  if two uploads land at the same instant.
- Indexes on `file_hash`, `created_at`, `document_type`, and `category` are
  created on startup. That is idempotent, so it runs harmlessly every time.

## PostgreSQL removal (done)

MongoDB is now the only backend. The PostgreSQL layer has been deleted:
`app/database.py`, `app/models.py`, `app/routers/documents.py`, the
`DB_BACKEND` switch and `else:` branch in `app/main.py`, the PostgreSQL
branch of `/health`, `DocumentOut` in `app/schemas.py`, the `sqlalchemy` and
`psycopg2-binary` requirements, and the `DB_*` block in `.env` /
`.env.example`.

`app/storage.py` is shared and stayed. `backend/verify_*.py` still pass.

The old `doc_collection_dev` PostgreSQL database was left alone — it still
holds two rows of early smoke-test data (`ABC Bank` / `XYZ`, `sample.pdf`),
which the application never used and can no longer reach. Drop that database
by hand whenever you want the server space back.

# PDF Splitter

Cuts one large combined PDF into individual documents, using an Excel index
that lists each document's page range and its metadata.

This is a standalone operator tool. It does not import from the FastAPI backend
and the backend does not import from it — it has its own dependencies and its
own virtual environment, so nothing about the running application changes when
this is installed or updated.

Its guiding rule: **a row is only split out when its page range can be read one
way and one way only.** Anything invalid or ambiguous is skipped, listed on
screen, and recorded in a CSV report — never guessed at. A wrong guess produces
a correct-looking PDF containing the wrong pages, which is far worse than
producing nothing for that row.

## One-time setup

From the project root:

```
python -m venv scripts\pdf_splitter\.venv
scripts\pdf_splitter\.venv\Scripts\python.exe -m pip install -r scripts\pdf_splitter\requirements.txt
```

This is deliberately separate from `backend\venv`. The splitter needs `pypdf`
and `openpyxl`; the backend needs neither.

## Usage

```
.\scripts\pdf_splitter\split.ps1 <combined.pdf> <index.xlsx> -Out <folder>
```

`split.ps1` calls the tool's own interpreter directly, so it works whether or
not any venv is activated (the same reason `backend\run.ps1` exists). Any extra
flag is passed straight through:

```
.\scripts\pdf_splitter\split.ps1 combined.pdf index.xlsx -DryRun
.\scripts\pdf_splitter\split.ps1 combined.pdf index.xlsx -Out .\output\rail --number-prefix
```

Without PowerShell, or from another script:

```
scripts\pdf_splitter\.venv\Scripts\python.exe scripts\pdf_splitter\split.py combined.pdf index.xlsx --out output\rail
```

**Always start with `-DryRun`.** It prints exactly which file would be produced
from which pages, and which rows would be rejected, without writing anything.

Full flag list: `split.py --help`.

## What the index needs to look like

One row per document. Column headers can sit under a title block — the header
row is found automatically. Headers are matched case- and punctuation-
insensitively against a list of common spellings, so `Start Page`, `start_page`,
`From Page` and `Pg From` all work.

| Purpose | Recognised headers include |
| --- | --- |
| First page | Start Page, From Page, Pg From, First Page |
| Last page | End Page, To Page, Pg To, Last Page |
| Or both in one cell | Pages, Page Range, Page Nos — `12-18`, `12 to 18`, `12` |
| Document type | Document Type, Doc Type, Type |
| Client | Client, Client Name, Customer, Employer, Owner |
| Reference | Reference No, Ref, Letter No, LOI No, WO No, Document No |
| Project | Project Title, Name of Work, Title, Description |
| Date | Date, Dated, Document Date, Issue Date |
| Also read | Category, Contract Value, Department, Notes/Remarks |

Page numbers are the **printed position in the combined PDF, counting from 1** —
page 1 is the first page of the file.

Columns the tool does not recognise are listed on screen as ignored, so a
column you expected it to use never disappears silently.

### When the headers do not match

Copy `column_map.example.json`, edit it, and pass it with `--column-map`:

```json
{
  "Pg From": "start_page",
  "Name of Client / Employer": "client_name"
}
```

Keys are the exact header text in the sheet; values are the field names above.
Keys beginning with `_` are treated as comments.

Also useful: `--sheet "Index"` to pick a worksheet, and `--header-row 3` if
auto-detection picks the wrong row.

## How files are named

Default pattern:

```
{document_type} - {client_name} - {reference_number} - {project_title}.pdf
```

Blank fields drop out together with their separator, so a row with no reference
number becomes `Work Order - Acme - Quayside lighting.pdf`, not
`Work Order - Acme -  - Quayside lighting.pdf`.

Change it with `--name-template`, using any of: `document_type`, `category`,
`client_name`, `reference_number`, `project_title`, `contract_value`,
`document_date`, `department`, `notes`.

```
--name-template "{document_date} {client_name} ({document_type})"
```

Names are made safe for Windows automatically: `\ / : * ? " < > |` are replaced,
reserved names like `CON` are escaped, and names are truncated to
`--max-name-length` (default 120) so the full path stays workable. Two rows that
produce the same name get ` (2)`, ` (3)` — nothing is ever silently overwritten.

`--number-prefix` prefixes `001 `, `002 ` so the output folder sorts in index
order.

## Renaming an existing output afterwards

A finished run can be renamed in place to descriptive filenames built from
the metadata already recorded in its report — useful when the index left the
naming columns blank and the folder ended up full of "Work Order (3).pdf":

```
scripts\pdf_splitter\.venv\Scripts\python.exe -m pdf_splitter.rename ^
    output\PMC_split_documents\_split_report.csv --dry-run
```

(run from the `scripts` folder; drop `--dry-run` to apply). Names come out as
`{type}_{date}_REF-{reference}_{title}.pdf`, e.g.
`Work_Order_2021-05-20_REF-MSAMB-IT-ERPAMC-WO-984-21.pdf` — with any part
whose metadata is missing simply dropped, the reference lifted from a
"Ref ... / Contract No. ..." pattern in the notes column when the
reference_number column is blank, and a `_2`-style suffix keeping names
unique. A row with no usable metadata keeps its original name.

The report's `file_name` column is updated to match; each renamed row's old
name is kept in a new `previous_file_name` column, and the untouched report
is copied to `_split_report.before_rename.csv` the first time. PDF contents,
page ranges and every other report column are left exactly as they were, and
re-running is a no-op.

## What gets rejected, and why

| Reason | What to fix in the index |
| --- | --- |
| no page range given | The page columns are blank on that row. |
| page range could not be read | e.g. `12A`, or `3, 7, 9` — one document must be one continuous range. |
| page columns disagree | Start/end columns and a combined Pages column say different things. |
| start page missing | An end page with no start page. |
| end page missing | See `--infer-end-page` below. |
| end page before start page | Reversed range, e.g. 16–14. |
| start/end page out of range | The range runs past the end of the PDF, or starts below page 1. |
| page range overlaps another row | Two rows claim the same page; nothing says which is right. |
| no metadata to build a file name from | Every field in the name template is blank on that row. |

Every rejected row is printed with its reason and written to the report. Fix the
spreadsheet and run again — files already produced are left alone unless you
pass `--overwrite`.

### Opt-in assumptions (all off by default)

These exist because the alternative is fixing the same spreadsheet by hand
every time. Each is off unless you ask for it, and each is recorded in the
report when it is used.

- `--infer-end-page` — where an end page is blank, take it from where the next
  document starts (and the last row runs to the end of the PDF). Assumes the
  documents are contiguous and in page order. Still refuses when two rows share
  a start page.
- `--allow-overlaps` — keep overlapping rows instead of rejecting them. Use when
  an overlap is genuine, e.g. a covering letter counted inside its attachment.
- `--fallback-name-pages` — name a row with no usable metadata after its page
  range (`pages 13-15.pdf`) instead of rejecting it.

## Output

The output folder gets one PDF per accepted row, plus `_split_report.csv`, which
has one line for **every** index row — written, skipped, or rejected — with its
status, reason, page range and metadata. That report is the record of the run,
and it doubles as a checklist when logging the split documents into the app.

Exit codes, for scripting:

| Code | Meaning |
| --- | --- |
| 0 | Every index row produced a document. |
| 1 | The run could not start — bad PDF, unreadable index, bad arguments. |
| 2 | Finished, but some rows were rejected, skipped, or failed to write. |

## Trying it out

Generate a sample PDF and index (the index deliberately contains faulty rows so
you can see how they are handled):

```
scripts\pdf_splitter\.venv\Scripts\python.exe scripts\pdf_splitter\tests\make_sample.py sample
.\scripts\pdf_splitter\split.ps1 sample\combined_documents.pdf sample\document_index.xlsx -DryRun
```

## Tests

```
scripts\pdf_splitter\.venv\Scripts\python.exe -m unittest discover -s scripts\pdf_splitter\tests -t scripts
```

Standard-library `unittest` only — no test framework to install.

## Notes

- The source PDF is only ever read, never modified.
- A password-protected PDF stops the run with a clear message. Remove the
  password (open and re-save it) and run again.
- `.xls` (pre-2007) files are not supported; re-save the index as `.xlsx`.
- If the index workbook uses formulas, open and save it in Excel once before
  running, so the calculated values are stored in the file.

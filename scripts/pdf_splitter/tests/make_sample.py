"""Generates a sample combined PDF and index so the splitter can be tried out.

    python scripts\pdf_splitter\tests\make_sample.py <folder>

The sample index deliberately contains bad rows (blank pages, a reversed
range, an overlap, a range past the end of the PDF) so a first run shows what
the tool does with them.

The PDF is written by hand rather than with a rendering library, purely so the
sample needs no dependency beyond what the splitter already requires.
"""

import sys
from pathlib import Path

from openpyxl import Workbook

PAGE_WIDTH, PAGE_HEIGHT = 595, 842            # A4 in PDF points


def _pdf_bytes(page_count: int) -> bytes:
    """Build a minimal valid PDF whose pages read "Page 1", "Page 2", ..."""
    objects = []                              # 1-based object bodies

    font_obj = 3 + page_count * 2             # objects: catalog, pages, kids...
    page_ids = [3 + i * 2 for i in range(page_count)]

    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        "<< /Type /Pages /Count {n} /Kids [{kids}] >>".format(
            n=page_count, kids=" ".join("{i} 0 R".format(i=i) for i in page_ids)
        )
    )
    for number in range(1, page_count + 1):
        content_id = page_ids[number - 1] + 1
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
            "/Resources << /Font << /F1 {f} 0 R >> >> /Contents {c} 0 R >>".format(
                w=PAGE_WIDTH, h=PAGE_HEIGHT, f=font_obj, c=content_id
            )
        )
        stream = "BT /F1 36 Tf 72 {y} Td (Page {n}) Tj ET".format(
            y=PAGE_HEIGHT - 120, n=number
        )
        objects.append(
            "<< /Length {length} >>\nstream\n{stream}\nendstream".format(
                length=len(stream), stream=stream
            )
        )
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += "{n} 0 obj\n{body}\nendobj\n".format(n=number, body=body).encode("latin-1")

    xref_at = len(out)
    out += "xref\n0 {n}\n".format(n=len(objects) + 1).encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += "{o:010d} 00000 n \n".format(o=offset).encode("latin-1")
    out += (
        "trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".format(
            n=len(objects) + 1, x=xref_at
        ).encode("latin-1")
    )
    return bytes(out)


SAMPLE_ROWS = [
    # (start, end, type, client, ref, title, date, note about the row)
    (1, 3, "LOI", "Northern Rail", "LOI/2024/118", "Signalling upgrade, Phase 2", "2024-03-11"),
    (4, 6, "Work Order", "Northern Rail", "WO/2024/451", "Signalling upgrade, Phase 2", "2024-04-02"),
    (7, 9, "Completion Certificate", "Northern Rail", "CC/2025/077", "Signalling upgrade, Phase 2", "2025-01-20"),
    (10, 12, "LOI", "Harbour Authority", "LOI/2024/204", "Quayside lighting replacement", "2024-06-14"),
    (13, "", "Work Order", "Harbour Authority", "WO/2024/612", "Quayside lighting replacement", "2024-07-01"),
    (16, 14, "Completion Certificate", "Harbour Authority", "CC/2025/031", "Quayside lighting replacement", "2025-02-05"),
    (18, 20, "LOI", "City Water Board", "LOI/2025/009", "Pumping station refurbishment", "2025-01-08"),
    (19, 22, "Work Order", "City Water Board", "WO/2025/033", "Pumping station refurbishment", "2025-02-11"),
    (23, 40, "Completion Certificate", "City Water Board", "CC/2025/090", "Pumping station refurbishment", "2025-06-30"),
]


def write_sample(folder: Path, page_count: int = 24) -> None:
    folder.mkdir(parents=True, exist_ok=True)

    pdf_path = folder / "combined_documents.pdf"
    pdf_path.write_bytes(_pdf_bytes(page_count))

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Index"
    sheet.append(["Document Index — sample data"])          # a title row above the headers
    sheet.append([])
    sheet.append([
        "Start Page", "End Page", "Document Type", "Client Name",
        "Reference No", "Project Title", "Date",
    ])
    for row in SAMPLE_ROWS:
        sheet.append(list(row))

    index_path = folder / "document_index.xlsx"
    workbook.save(str(index_path))

    print("Wrote {pdf} ({n} pages)".format(pdf=pdf_path, n=page_count))
    print("Wrote {index} ({n} rows, some deliberately faulty)".format(
        index=index_path, n=len(SAMPLE_ROWS)))
    print()
    print("Try it with:")
    print("  .\\scripts\\pdf_splitter\\split.ps1 {pdf} {index} -DryRun".format(
        pdf=pdf_path, index=index_path))


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample")
    write_sample(target)

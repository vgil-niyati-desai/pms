"""Direct entry point, so the tool can be run by path from anywhere:

    python scripts\pdf_splitter\split.py <pdf> <index.xlsx> --out <folder>

Running a file that lives inside a package would normally break its relative
imports, so the package's parent folder is put on sys.path and the package is
imported by name instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_splitter.cli import main  # noqa: E402  (import needs the path above)

if __name__ == "__main__":
    raise SystemExit(main())

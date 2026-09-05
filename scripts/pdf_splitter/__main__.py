"""Allows `python -m pdf_splitter` when this folder's parent is on sys.path."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

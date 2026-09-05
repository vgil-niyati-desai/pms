"""Reusable splitter that cuts a large combined PDF into individual documents
using an Excel index that lists each document's page range and metadata.

Standalone by design: nothing here imports from the FastAPI app, and the app
does not import from here. It is a manual, operator-run tool.
"""

__version__ = "1.0.0"

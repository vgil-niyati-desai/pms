"""Schema migrations for the document collection system.

Each migration is a module in this package exposing:

    VERSION      a stable identifier, also the bookkeeping record's _id
    DESCRIPTION  one line saying what it does
    analyse(db)  read-only; returns a report dict, writes nothing
    apply(db)    performs the change and returns the same shape of report

MIGRATIONS below is the ordered list. Migrations are applied in that order
and each records itself in the `migrations` collection when it completes, so
running the runner again is a no-op rather than a second pass.

Nothing here copies or replaces a database, and no migration touches a
collection outside the one it names.
"""

from . import (
    m0001_document_project_id,
    m0002_standardise_contract_type,
    m0003_document_employee_id,
    m0004_document_tender_id,
    m0005_tender_cited_project_ids,
)

MIGRATIONS = [
    m0001_document_project_id,
    m0002_standardise_contract_type,
    m0003_document_employee_id,
    m0004_document_tender_id,
    m0005_tender_cited_project_ids,
]

# The collection holding one record per applied migration. Deliberately its
# own collection rather than a flag on an existing document: bookkeeping is
# not data, and it has to survive anything the migrations themselves do.
BOOKKEEPING_COLLECTION = "migrations"


def applied_versions(db):
    """The set of migration versions already recorded as complete."""
    if BOOKKEEPING_COLLECTION not in db.list_collection_names():
        return set()
    return {record["_id"] for record in db[BOOKKEEPING_COLLECTION].find({}, {"_id": 1})}

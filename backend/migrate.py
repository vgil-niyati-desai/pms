"""Migration runner.

Usage, from the backend folder:

    .\\venv\\Scripts\\python.exe migrate.py --status     what has been applied
    .\\venv\\Scripts\\python.exe migrate.py --dry-run    what would change
    .\\venv\\Scripts\\python.exe migrate.py              apply what is pending
    .\\venv\\Scripts\\python.exe migrate.py --force      re-run even if recorded

It works against whatever MONGO_* in backend/.env points at, in place: it
never copies, renames or replaces a database, and each migration only touches
the collections it names.

A migration that reports inconsistent data applies the part it is sure about
and leaves the rest alone, printing what it skipped. It does not fail the run
-- those cases need a decision, not a crashed script.
"""

import argparse
import sys

from app import mongodb
from migrations import MIGRATIONS, applied_versions


def _print_report(migration, report):
    """Each migration formats its own report; the runner only indents it.

    A migration knows what its numbers mean and what it refused to do; a
    printer here would have to grow a branch per migration to say it.
    """
    for line in migration.report_lines(report):
        print(("    " + line) if line else "")


def main():
    parser = argparse.ArgumentParser(description="Apply pending database migrations.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change; write nothing.")
    parser.add_argument("--status", action="store_true",
                        help="List migrations and whether they have been applied.")
    parser.add_argument("--force", action="store_true",
                        help="Re-run a migration even if it is already recorded.")
    args = parser.parse_args()

    reachable, error = mongodb.ping()
    if not reachable:
        print("Cannot reach MongoDB at %s: %s" % (mongodb.safe_uri(), error))
        print("Check MONGO_* in backend/.env, and see SETUP.md section 1b.")
        return 1

    db = mongodb.get_database()
    print("database: %s / %s\n" % (mongodb.safe_uri(), mongodb.MONGO_DB_NAME))

    done = applied_versions(db)

    if args.status:
        for migration in MIGRATIONS:
            state = "applied" if migration.VERSION in done else "pending"
            print("  [%-7s] %s" % (state, migration.VERSION))
            print("            %s" % migration.DESCRIPTION)
        return 0

    for migration in MIGRATIONS:
        already = migration.VERSION in done
        if already and not args.force:
            print("  [skip   ] %s (already applied)" % migration.VERSION)
            continue

        print("  [%s] %s" % ("dry-run" if args.dry_run else "apply  ", migration.VERSION))
        print("            %s" % migration.DESCRIPTION)

        if args.dry_run:
            _print_report(migration, migration.analyse(db))
            print("\n    nothing was written.")
            continue

        report = migration.apply(db)
        _print_report(migration, report)
        migration.record(db, report)
        print("\n    recorded as applied.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

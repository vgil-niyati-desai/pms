"""Prompt for an initial local development administrator account.

Run with `python -m app.create_admin` from backend/. This tool refuses
non-local MongoDB targets and database names not clearly marked for development.
"""

import os
import sys
from getpass import getpass

from pymongo.errors import DuplicateKeyError

from . import auth, mongodb


def _require_local_database(allow_remote: bool) -> None:
    database_is_local = (
        not os.getenv("MONGO_URI", "").strip()
        and mongodb.MONGO_HOST.strip().strip("[]").lower()
        in {"localhost", "127.0.0.1", "::1"}
    )
    name_is_development = mongodb.MONGO_DB_NAME.lower().endswith(
        ("_dev", "_local", "_test")
    )
    if database_is_local and name_is_development:
        return
    if allow_remote:
        expected = f"CREATE ADMIN IN DATABASE {mongodb.MONGO_DB_NAME}"
        if input(f"Type exactly '{expected}' to continue: ") == expected:
            return
        raise RuntimeError("Database confirmation did not match.")
    if os.getenv("MONGO_URI", "").strip():
        raise RuntimeError("Admin bootstrap refuses a configured MONGO_URI by default.")
    if not database_is_local:
        raise RuntimeError("Admin bootstrap is restricted to a local MongoDB host by default.")
    if not name_is_development:
        raise RuntimeError(
            "Admin bootstrap requires a database name ending in _dev, _local, or _test."
        )


def main() -> int:
    if any(argument != "--allow-remote" for argument in sys.argv[1:]):
        print("Usage: python -m app.create_admin [--allow-remote]", file=sys.stderr)
        return 2
    allow_remote = "--allow-remote" in sys.argv[1:]
    try:
        _require_local_database(allow_remote)
    except RuntimeError as exc:
        print(f"Refusing to create an account: {exc}", file=sys.stderr)
        return 2

    username = input("Admin username or email: ").strip()
    password = getpass("Password (at least 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12:
        print("Password must contain at least 12 characters.", file=sys.stderr)
        return 2
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    mongodb.ensure_auth_indexes()
    try:
        auth.create_user(username, password)
    except DuplicateKeyError:
        print("That username already exists.", file=sys.stderr)
        return 2

    print(f"Created the initial administrator account '{auth.normalise_username(username)}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

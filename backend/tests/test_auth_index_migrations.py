"""Migrations 0007 (login_throttle TTL index) and 0008 (users/sessions indexes).

Every test runs against its own in-memory mongomock database; nothing here
connects to MongoDB. The runner tests patch mongodb.ping/get_database so
migrate.py's real code path runs against that same in-memory database.
"""

import contextlib
import io
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import mongomock
from bson import ObjectId

import migrate
from app import mongodb
from migrations import MIGRATIONS, m0007_login_throttle_indexes as m0007
from migrations import m0008_auth_indexes as m0008

_WRITE_METHODS = (
    "insert_one", "insert_many", "update_one", "update_many", "replace_one",
    "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace",
    "find_one_and_delete", "bulk_write", "create_index", "create_indexes",
    "drop_index", "drop_indexes", "drop",
)


@contextlib.contextmanager
def forbid_writes():
    """Any write through mongomock inside this block fails the test."""
    def refuse(name):
        def method(*args, **kwargs):
            raise AssertionError("unexpected write: %s" % name)
        return method

    with contextlib.ExitStack() as stack:
        for name in _WRITE_METHODS:
            stack.enter_context(patch.object(mongomock.Collection, name, refuse(name)))
        for name in ("create_collection", "drop_collection"):
            stack.enter_context(patch.object(mongomock.Database, name, refuse(name)))
        yield


def snapshot(db):
    """Every collection's documents and index definitions, for before/after."""
    return {
        name: (list(db[name].find({})), db[name].index_information())
        for name in sorted(db.list_collection_names())
    }


def index_definitions(collection):
    """name -> index_information() entry, minus the server-supplied ns field."""
    return {
        name: {k: v for k, v in info.items() if k != "ns"}
        for name, info in collection.index_information().items()
    }


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient(tz_aware=True)["pms_migration_test"]

    def run_migrate(self, *args):
        """Run migrate.main() against self.db; returns (exit code, output)."""
        output = io.StringIO()
        with patch.object(mongodb, "ping", return_value=(True, None)), patch.object(
            mongodb, "get_database", return_value=self.db
        ), patch.object(sys, "argv", ["migrate.py", *args]), contextlib.redirect_stdout(output):
            code = migrate.main()
        return code, output.getvalue()


class RegistrationTests(unittest.TestCase):
    def test_0007_and_0008_are_registered_in_order(self):
        versions = [migration.VERSION for migration in MIGRATIONS]
        position = versions.index(m0007.VERSION)
        self.assertEqual(versions[position : position + 2], [m0007.VERSION, m0008.VERSION])
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(versions, sorted(versions))


class LoginThrottleIndexMigrationTests(MigrationTestCase):
    def test_analyse_is_read_only_and_reports_missing_index(self):
        with forbid_writes():
            report = m0007.analyse(self.db)
        self.assertFalse(report["collection_exists"])
        self.assertFalse(report["index_already_present"])
        self.assertEqual(self.db.list_collection_names(), [])

    def test_dry_run_lines_say_what_would_happen_and_never_claim_creation(self):
        missing_lines = m0007.report_lines(m0007.analyse(self.db))
        self.assertIn("missing -- would be created", missing_lines[-1])
        self.assertNotIn(": created", "\n".join(missing_lines))

        self.db["login_throttle"].create_index(
            [("window_expires_at", 1)], name=m0007.INDEX_NAME, expireAfterSeconds=0
        )
        present_lines = m0007.report_lines(m0007.analyse(self.db))
        self.assertIn("already present -- nothing to create", present_lines[-1])

    def test_apply_creates_the_ttl_index_and_reports_creation(self):
        report = m0007.apply(self.db)
        self.assertFalse(report["index_already_present"])
        self.assertTrue(report["index_created"])
        self.assertIn(": created", m0007.report_lines(report)[-1])
        info = self.db["login_throttle"].index_information()[m0007.INDEX_NAME]
        self.assertEqual(info["key"], [("window_expires_at", 1)])
        self.assertEqual(info["expireAfterSeconds"], 0)

    def test_apply_is_idempotent_and_leaves_documents_alone(self):
        self.db["login_throttle"].insert_one(
            {"_id": "ip:abc:1", "window_expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc)}
        )
        m0007.apply(self.db)
        before = snapshot(self.db)
        second = m0007.apply(self.db)
        self.assertEqual(snapshot(self.db), before)
        self.assertTrue(second["index_already_present"])
        self.assertFalse(second["index_created"])
        self.assertIn("already present -- left as is", m0007.report_lines(second)[-1])

    def test_index_matches_the_one_startup_creates(self):
        startup_db = mongomock.MongoClient(tz_aware=True)["pms_startup_test"]
        with patch.object(
            mongodb, "get_login_throttle_collection", return_value=startup_db["login_throttle"]
        ):
            mongodb.ensure_login_throttle_indexes()
        m0007.apply(self.db)
        self.assertEqual(
            index_definitions(self.db["login_throttle"]),
            index_definitions(startup_db["login_throttle"]),
        )

    def test_index_created_by_startup_first_is_reported_as_present(self):
        with patch.object(
            mongodb, "get_login_throttle_collection", return_value=self.db["login_throttle"]
        ):
            mongodb.ensure_login_throttle_indexes()
        report = m0007.apply(self.db)
        self.assertTrue(report["index_already_present"])
        self.assertFalse(report["index_created"])


class AuthIndexMigrationTests(MigrationTestCase):
    def seed_valid_data(self):
        self.db["users"].insert_many(
            [
                {"username": "admin@example.test", "role": "Admin", "is_active": True},
                {"username": "hod@example.test", "role": "HOD", "is_active": True},
            ]
        )
        self.db["sessions"].insert_many(
            [
                {"token_hash": "a" * 64, "user_id": ObjectId(), "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc)},
                {"token_hash": "b" * 64, "user_id": ObjectId(), "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc)},
                # auth.py's account-change lock record: no token_hash, no expiry.
                {"_id": "admin-account-mutation-lock", "lock_until": datetime(2000, 1, 1, tzinfo=timezone.utc)},
            ]
        )

    def assert_refuses_without_writing(self, expected_fragment):
        before = snapshot(self.db)
        with self.assertRaises(m0008.AuthIndexConflict) as raised:
            m0008.apply(self.db)
        self.assertIn(expected_fragment, str(raised.exception))
        self.assertIn("nothing was written", str(raised.exception))
        self.assertEqual(snapshot(self.db), before)

    def test_analyse_is_read_only_on_conflicting_data(self):
        self.seed_valid_data()
        self.db["users"].insert_one({"username": "admin@example.test"})
        self.db["sessions"].insert_one({"user_id": ObjectId()})
        before = snapshot(self.db)
        with forbid_writes():
            report = m0008.analyse(self.db)
        self.assertEqual(snapshot(self.db), before)
        self.assertEqual(report["shared_usernames"], 1)
        self.assertEqual(report["sessions_without_token_hash"], 2)
        self.assertEqual(len(report["conflicts"]), 2)

    def test_apply_creates_all_three_indexes_on_valid_data(self):
        self.seed_valid_data()
        documents_before = {name: list(self.db[name].find({})) for name in ("users", "sessions")}
        report = m0008.apply(self.db)
        self.assertEqual(
            report["created"],
            ["ux_users_username", "ux_sessions_token_hash", "ix_sessions_expires_at"],
        )
        self.assertEqual(report["sessions_without_token_hash"], 1)
        self.assertEqual(
            {name: list(self.db[name].find({})) for name in ("users", "sessions")},
            documents_before,
        )
        users = self.db["users"].index_information()
        sessions = self.db["sessions"].index_information()
        self.assertTrue(users["ux_users_username"]["unique"])
        self.assertTrue(sessions["ux_sessions_token_hash"]["unique"])
        self.assertEqual(sessions["ix_sessions_expires_at"]["expireAfterSeconds"], 0)
        lines = m0008.report_lines(report)
        self.assertEqual(sum(": created" in line for line in lines), 3)

    def test_apply_on_an_empty_database_creates_the_collections_indexes(self):
        report = m0008.apply(self.db)
        self.assertEqual(len(report["created"]), 3)
        self.assertEqual(report["conflicts"], [])

    def test_apply_is_idempotent(self):
        self.seed_valid_data()
        m0008.apply(self.db)
        before = snapshot(self.db)
        second = m0008.apply(self.db)
        self.assertEqual(snapshot(self.db), before)
        self.assertEqual(second["created"], [])
        self.assertTrue(all(index["state"] == "present" for index in second["indexes"]))
        self.assertTrue(
            all("already present -- left as is" in line for line in m0008.report_lines(second)[:3])
        )

    def test_indexes_match_the_ones_startup_creates(self):
        startup_db = mongomock.MongoClient(tz_aware=True)["pms_startup_test"]
        with patch.object(
            mongodb, "get_users_collection", return_value=startup_db["users"]
        ), patch.object(mongodb, "get_sessions_collection", return_value=startup_db["sessions"]):
            mongodb.ensure_auth_indexes()
        m0008.apply(self.db)
        for name in ("users", "sessions"):
            with self.subTest(collection=name):
                self.assertEqual(
                    index_definitions(self.db[name]), index_definitions(startup_db[name])
                )

    def test_indexes_created_by_startup_first_are_reported_as_present(self):
        with patch.object(
            mongodb, "get_users_collection", return_value=self.db["users"]
        ), patch.object(mongodb, "get_sessions_collection", return_value=self.db["sessions"]):
            mongodb.ensure_auth_indexes()
        report = m0008.apply(self.db)
        self.assertEqual(report["created"], [])
        self.assertEqual(report["conflicts"], [])

    def test_duplicate_usernames_refuse(self):
        self.seed_valid_data()
        self.db["users"].insert_one({"username": "hod@example.test", "role": "Normal User"})
        self.assert_refuses_without_writing("username value(s) are shared by 2 accounts")

    def test_two_accounts_without_a_username_refuse(self):
        self.db["users"].insert_many([{"role": "HOD"}, {"role": "Normal User"}])
        self.assert_refuses_without_writing("shared by 2 accounts")

    def test_duplicate_session_token_hashes_refuse(self):
        self.seed_valid_data()
        self.db["sessions"].insert_one({"token_hash": "a" * 64, "user_id": ObjectId()})
        self.assert_refuses_without_writing("token hash(es) are shared by 2 sessions")

    def test_more_than_one_session_without_token_hash_refuses(self):
        self.seed_valid_data()
        self.db["sessions"].insert_one({"token_hash": None, "user_id": ObjectId()})
        self.assert_refuses_without_writing("2 sessions documents have no token_hash")

    def test_same_name_with_different_options_is_a_conflict(self):
        self.db["users"].create_index([("username", 1)], name="ux_users_username")
        self.assert_refuses_without_writing("ux_users_username already exists with a different definition")

    def test_same_key_under_another_name_is_a_conflict(self):
        self.db["users"].create_index([("username", 1)], name="username_1", unique=True)
        self.assert_refuses_without_writing("users already has index username_1")

    def test_ttl_with_a_different_expiry_is_a_conflict(self):
        self.db["sessions"].create_index(
            [("expires_at", 1)], name="ix_sessions_expires_at", expireAfterSeconds=3600
        )
        self.assert_refuses_without_writing("sessions.ix_sessions_expires_at already exists")

    def test_server_bookkeeping_fields_do_not_count_as_a_difference(self):
        """Real servers may echo ns/background, which mongomock never returns."""
        server_indexes = {
            "users": [
                {"v": 2, "key": {"_id": 1}, "name": "_id_", "ns": "pms.users"},
                {"v": 2, "key": {"username": 1}, "name": "ux_users_username", "unique": True,
                 "ns": "pms.users", "background": True},
            ],
            "sessions": [
                {"v": 2, "key": {"_id": 1}, "name": "_id_", "ns": "pms.sessions"},
                {"v": 2, "key": {"token_hash": 1}, "name": "ux_sessions_token_hash",
                 "unique": True, "ns": "pms.sessions"},
                {"v": 2, "key": {"expires_at": 1}, "name": "ix_sessions_expires_at",
                 "expireAfterSeconds": 0, "ns": "pms.sessions", "background": False},
            ],
        }

        def list_indexes(collection):
            return iter(server_indexes.get(collection.name, []))

        with patch.object(mongomock.Collection, "list_indexes", list_indexes):
            report = m0008.analyse(self.db)
        self.assertEqual(report["conflicts"], [])
        self.assertTrue(all(index["state"] == "present" for index in report["indexes"]))

    def test_dry_run_lines_never_claim_creation(self):
        lines = m0008.report_lines(m0008.analyse(self.db))
        self.assertEqual(sum("missing -- would be created" in line for line in lines), 3)
        self.assertNotIn(": created", "\n".join(lines))

        self.db["users"].insert_many([{"username": "x"}, {"username": "x"}])
        conflict_lines = "\n".join(m0008.report_lines(m0008.analyse(self.db)))
        self.assertIn("applying would refuse and write nothing", conflict_lines)

    def test_record_keeps_what_the_first_run_created(self):
        first = m0008.apply(self.db)
        m0008.record(self.db, first)
        m0008.record(self.db, m0008.apply(self.db))
        entry = self.db["migrations"].find_one({"_id": m0008.VERSION})
        self.assertEqual(len(entry["first_created"]), 3)
        self.assertEqual(entry["last_created"], [])


class RunnerTests(MigrationTestCase):
    def test_dry_run_writes_nothing_and_reports_both_migrations(self):
        code, output = self.run_migrate("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.db.list_collection_names(), [])
        self.assertIn("[dry-run] %s" % m0007.VERSION, output)
        self.assertIn("[dry-run] %s" % m0008.VERSION, output)
        self.assertIn("missing -- would be created", output)
        self.assertNotIn(": created", output)
        self.assertNotIn("ensured", output.split(m0007.VERSION, 1)[1])

    def test_status_writes_nothing_and_lists_both_as_pending(self):
        code, output = self.run_migrate("--status")
        self.assertEqual(code, 0)
        self.assertEqual(self.db.list_collection_names(), [])
        self.assertIn("[pending] %s" % m0007.VERSION, output)
        self.assertIn("[pending] %s" % m0008.VERSION, output)

    def test_apply_records_both_and_a_second_run_skips_them(self):
        code, output = self.run_migrate()
        self.assertEqual(code, 0)
        recorded = {record["_id"] for record in self.db["migrations"].find({})}
        self.assertTrue({m0007.VERSION, m0008.VERSION} <= recorded)

        code, output = self.run_migrate()
        self.assertIn("[skip   ] %s (already applied)" % m0008.VERSION, output)

    def test_conflict_stops_the_run_and_leaves_0008_pending(self):
        self.db["users"].insert_many([{"username": "x"}, {"username": "x"}])
        users_before = list(self.db["users"].find({}))
        with self.assertRaises(m0008.AuthIndexConflict):
            self.run_migrate()
        self.assertIsNotNone(self.db["migrations"].find_one({"_id": m0007.VERSION}))
        self.assertIsNone(self.db["migrations"].find_one({"_id": m0008.VERSION}))
        self.assertEqual(list(self.db["users"].find({})), users_before)
        self.assertEqual(list(self.db["users"].index_information()), ["_id_"])
        self.assertNotIn("ix_sessions_expires_at", self.db["sessions"].index_information())


if __name__ == "__main__":
    unittest.main()

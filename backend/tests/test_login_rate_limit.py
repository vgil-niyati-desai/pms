import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mongomock
from fastapi.testclient import TestClient

from app import auth, main, mongodb, rate_limit


# Fixed "now" values used below are deliberately far in the future (not just
# an arbitrary fixed date): login_throttle's TTL index is real in mongomock,
# and it expires documents against the actual wall clock regardless of what
# app/rate_limit.py's patched _now() returns. A fixed date already in the
# past relative to real time gets its documents silently deleted by that
# TTL sweep before the test can observe them.
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


class LoginRateLimitTests(unittest.TestCase):
    """Dedicated tests for app/rate_limit.py and its wiring into /auth/login.

    See backend/tests/test_auth.py for the rest of the authentication
    behavior -- this file only covers rate limiting.
    """

    @classmethod
    def setUpClass(cls):
        cls.previous_client = mongodb._client
        cls.previous_ping = mongodb.ping
        cls.previous_cookie_secure = auth.COOKIE_SECURE
        mongodb._client = mongomock.MongoClient(tz_aware=True)
        mongodb.ping = lambda: (True, None)
        auth.COOKIE_SECURE = False

        cls.database = mongodb.get_database()
        cls.users = cls.database[mongodb.MONGO_USERS_COLLECTION_NAME]
        cls.sessions = cls.database[mongodb.MONGO_SESSIONS_COLLECTION_NAME]
        cls.throttle = cls.database[mongodb.MONGO_LOGIN_THROTTLE_COLLECTION_NAME]
        main.app.dependency_overrides[mongodb.get_users_collection] = lambda: cls.users
        main.app.dependency_overrides[mongodb.get_sessions_collection] = lambda: cls.sessions
        main.app.dependency_overrides[mongodb.get_login_throttle_collection] = lambda: cls.throttle
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        main.app.dependency_overrides.clear()
        mongodb._client = cls.previous_client
        mongodb.ping = cls.previous_ping
        auth.COOKIE_SECURE = cls.previous_cookie_secure

    def setUp(self):
        self.client.cookies.clear()
        self.users.delete_many({})
        self.sessions.delete_many({})
        self.throttle.delete_many({})
        auth.create_user("admin@example.test", "correct horse battery staple", self.users)

    def login(self, username="admin@example.test", password="correct horse battery staple"):
        return self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"Origin": "http://localhost:5173"},
        )

    # -- per-IP+username hard limit -----------------------------------

    def test_ipuser_hard_limit_blocks_then_recovers_after_window(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 3), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            base = FAR_FUTURE
            with patch("app.rate_limit._now", return_value=base):
                for _ in range(3):
                    self.assertEqual(self.login(password="wrong").status_code, 401)

                blocked = self.login(password="wrong")
                self.assertEqual(blocked.status_code, 429)
                self.assertEqual(blocked.json()["detail"], rate_limit.GENERIC_RATE_LIMIT_ERROR)
                retry_after = int(blocked.headers["Retry-After"])
                self.assertGreater(retry_after, 0)

                # The hard limit blocks indiscriminately once tripped, even
                # with the correct password.
                still_blocked = self.login()
                self.assertEqual(still_blocked.status_code, 429)

            later = base + timedelta(minutes=rate_limit.IPUSER_WINDOW_MINUTES, seconds=1)
            with patch("app.rate_limit._now", return_value=later):
                recovered = self.login()
                self.assertEqual(recovered.status_code, 200)

    # -- per-IP hard limit, across different usernames -----------------

    def test_ip_wide_hard_limit_blocks_across_different_usernames(self):
        with patch.object(rate_limit, "IP_MAX_FAILURES", 3), patch.object(
            rate_limit, "IPUSER_MAX_FAILURES", 1000
        ):
            base = FAR_FUTURE
            with patch("app.rate_limit._now", return_value=base):
                for index in range(3):
                    response = self.login(username=f"nouser{index}@example.test", password="wrong")
                    self.assertEqual(response.status_code, 401)

                blocked = self.login(username="yet-another@example.test", password="wrong")
                self.assertEqual(blocked.status_code, 429)

    # -- username-wide soft delay: never blocks, always capped ----------

    def test_username_soft_delay_never_blocks_and_is_capped(self):
        with patch.object(rate_limit, "USER_SOFT_THRESHOLD", 2), patch.object(
            rate_limit, "USER_SOFT_DELAY_STEP_SECONDS", 1.0
        ), patch.object(rate_limit, "USER_SOFT_DELAY_CAP_SECONDS", 3.0), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ), patch.object(rate_limit, "IPUSER_MAX_FAILURES", 1000):
            now = FAR_FUTURE
            user_hash = rate_limit.hash_value("admin@example.test")

            # Ten different IPs failing against the same account -- the
            # scenario a per-username hard lockout would be vulnerable to.
            for index in range(10):
                reservation = rate_limit.reserve_hard_limits(
                    self.throttle, rate_limit.hash_value(f"203.0.113.{index}"), user_hash, now
                )
                rate_limit.commit_failure(self.throttle, reservation, user_hash, now)

            with patch("app.rate_limit._now", return_value=now), patch(
                "app.rate_limit.time.sleep"
            ) as sleep_mock:
                # The real owner, from a fresh IP, with the correct password,
                # still gets in.
                response = self.login()
                self.assertEqual(response.status_code, 200)
                sleep_mock.assert_called_once()
                delay = sleep_mock.call_args[0][0]
                self.assertAlmostEqual(delay, 3.0)  # (10 - 2) * 1.0 = 8, capped to 3.0

    # -- success never spends the failure budget -------------------------

    def test_successful_login_near_limit_does_not_consume_failure_budget(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 3), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            now = FAR_FUTURE
            with patch("app.rate_limit._now", return_value=now):
                for _ in range(2):
                    self.assertEqual(self.login(password="wrong").status_code, 401)

                self.assertEqual(self.login().status_code, 200)
                self.client.cookies.clear()

                ip_hash = rate_limit.hash_value("testclient")
                user_hash = rate_limit.hash_value("admin@example.test")
                bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
                key = rate_limit._ipuser_key(ip_hash, user_hash, bucket)
                doc = self.throttle.find_one({"_id": key})
                self.assertEqual(doc["fail_count"], 2)
                self.assertEqual(doc["reservations"], [])

                # The success didn't spend the budget: one more failure is
                # still accepted before the limit trips.
                self.assertEqual(self.login(password="wrong").status_code, 401)
                self.assertEqual(self.login(password="wrong").status_code, 429)

    # -- concurrency: the hard limit cannot be exceeded by a race --------

    def test_concurrent_reservations_cannot_exceed_hard_limit(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 5), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            now = FAR_FUTURE
            ip_hash = rate_limit.hash_value("attacker-ip")
            user_hash = rate_limit.hash_value("victim@example.test")

            admitted = []
            errors = []
            lock = threading.Lock()

            def attempt():
                try:
                    reservation = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)
                except rate_limit.RateLimited:
                    return
                except Exception as exc:  # pragma: no cover - surfaced via assertion below
                    with lock:
                        errors.append(exc)
                    return
                with lock:
                    admitted.append(reservation)

            threads = [threading.Thread(target=attempt) for _ in range(25)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertEqual(len(admitted), 5)

            for reservation in admitted:
                rate_limit.commit_failure(self.throttle, reservation, user_hash, now)

            bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
            key = rate_limit._ipuser_key(ip_hash, user_hash, bucket)
            doc = self.throttle.find_one({"_id": key})
            self.assertEqual(doc["fail_count"], 5)
            self.assertEqual(doc["reservations"], [])

    def test_concurrent_mixed_success_and_failure_respects_limit(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 5), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            now = FAR_FUTURE
            ip_hash = rate_limit.hash_value("mixed-ip")
            user_hash = rate_limit.hash_value("victim2@example.test")

            results = []
            lock = threading.Lock()

            def attempt(should_fail):
                try:
                    reservation = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)
                except rate_limit.RateLimited:
                    with lock:
                        results.append("blocked")
                    return
                if should_fail:
                    rate_limit.commit_failure(self.throttle, reservation, user_hash, now)
                    with lock:
                        results.append("failure")
                else:
                    rate_limit.release_hard_limits(self.throttle, reservation)
                    with lock:
                        results.append("success")

            threads = [threading.Thread(target=attempt, args=(index % 2 == 0,)) for index in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # A success releases its reservation immediately, freeing room for
            # another attempt -- so the *total* number ever admitted over the
            # whole run is not bounded by the limit (that would wrongly treat
            # a hard limit on *concurrently outstanding* attempts as a limit
            # on lifetime attempts, which would make the account limit's own
            # legitimate logins). What the limit actually guarantees, and
            # what is safe to assert here, is that fail_count -- confirmed
            # failures, which never get released -- can never exceed the cap:
            # fail_count(t) + live_reservations(t) <= MAX holds at every
            # instant, and a release only ever shrinks that sum, so
            # fail_count alone can never cross MAX regardless of how many
            # successes free up capacity in between.
            self.assertEqual(len(results), 20)
            self.assertEqual(results.count("blocked") + results.count("failure") + results.count("success"), 20)

            bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
            key = rate_limit._ipuser_key(ip_hash, user_hash, bucket)
            doc = self.throttle.find_one({"_id": key})
            self.assertEqual(doc["fail_count"], results.count("failure"))
            self.assertLessEqual(doc["fail_count"], rate_limit.IPUSER_MAX_FAILURES)
            self.assertEqual(doc["reservations"], [])

    # -- exceptions release without being charged as a failure -----------

    def test_exception_during_verification_releases_without_counting_as_failure(self):
        now = FAR_FUTURE
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 3), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ), patch("app.rate_limit._now", return_value=now), patch(
            "app.auth.verify_password", side_effect=RuntimeError("boom")
        ):
            response = self.login()
            self.assertEqual(response.status_code, 500)

        ip_hash = rate_limit.hash_value("testclient")
        user_hash = rate_limit.hash_value("admin@example.test")
        bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
        key = rate_limit._ipuser_key(ip_hash, user_hash, bucket)
        doc = self.throttle.find_one({"_id": key})
        self.assertEqual(doc["fail_count"], 0)
        self.assertEqual(doc["reservations"], [])

        with patch("app.rate_limit._now", return_value=now):
            self.assertEqual(self.login().status_code, 200)

    # -- exception during the soft delay releases, like verification does -

    def test_exception_during_soft_delay_releases_without_counting_as_failure(self):
        now = FAR_FUTURE
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 3), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ), patch("app.rate_limit._now", return_value=now), patch(
            "app.rate_limit.apply_user_soft_delay", side_effect=RuntimeError("boom")
        ):
            response = self.login()
            self.assertEqual(response.status_code, 500)

        ip_hash = rate_limit.hash_value("testclient")
        user_hash = rate_limit.hash_value("admin@example.test")
        ip_bucket = rate_limit._bucket(rate_limit.IP_WINDOW_MINUTES, now)
        ip_key = rate_limit._ip_key(ip_hash, ip_bucket)
        ipuser_bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
        ipuser_key = rate_limit._ipuser_key(ip_hash, user_hash, ipuser_bucket)

        ip_doc = self.throttle.find_one({"_id": ip_key})
        ipuser_doc = self.throttle.find_one({"_id": ipuser_key})
        self.assertEqual(ip_doc["fail_count"], 0)
        self.assertEqual(ip_doc["reservations"], [])
        self.assertEqual(ipuser_doc["fail_count"], 0)
        self.assertEqual(ipuser_doc["reservations"], [])

        with patch("app.rate_limit._now", return_value=now):
            self.assertEqual(self.login().status_code, 200)

    # -- an unexpected failure acquiring the IP+username reservation -----
    # -- cannot leak the IP-level one it was taken alongside -------------

    def test_reserve_hard_limits_releases_ip_reservation_if_ipuser_step_raises(self):
        now = FAR_FUTURE
        ip_hash = rate_limit.hash_value("flaky-ip")
        user_hash = rate_limit.hash_value("flaky-user@example.test")

        original_try_reserve = rate_limit._try_reserve

        def flaky(collection, key, max_failures, current_now):
            if key.startswith("ipuser:"):
                raise RuntimeError("boom")
            return original_try_reserve(collection, key, max_failures, current_now)

        with patch.object(rate_limit, "_try_reserve", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)

        ip_bucket = rate_limit._bucket(rate_limit.IP_WINDOW_MINUTES, now)
        ip_key = rate_limit._ip_key(ip_hash, ip_bucket)
        doc = self.throttle.find_one({"_id": ip_key})
        self.assertEqual(doc["reservations"], [])
        self.assertEqual(doc["fail_count"], 0)

        # The slot the failed attempt gave back is available to the next one.
        reservation = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)
        self.assertIsNotNone(reservation)

    # -- crash recovery: a leaked reservation self-expires ----------------

    def test_crashed_reservation_recovers_after_its_lease_expires(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 1), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            now = FAR_FUTURE
            ip_hash = rate_limit.hash_value("crash-ip")
            user_hash = rate_limit.hash_value("admin@example.test")

            # A worker reserves a slot and crashes before it can release it.
            crashed = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)

            with self.assertRaises(rate_limit.RateLimited):
                rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)

            still_within_lease = now + timedelta(seconds=rate_limit.RESERVATION_LEASE_SECONDS - 1)
            with self.assertRaises(rate_limit.RateLimited):
                rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, still_within_lease)

            bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
            key = rate_limit._ipuser_key(ip_hash, user_hash, bucket)
            still_present = self.throttle.find_one({"_id": key})
            self.assertIn(
                crashed.ipuser_reservation_id,
                {entry["id"] for entry in still_present["reservations"]},
            )

            after_lease = now + timedelta(seconds=rate_limit.RESERVATION_LEASE_SECONDS + 1)
            recovered = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, after_lease)
            self.assertIsNotNone(recovered)

    # -- release is scoped to its own reservation id, and idempotent -----

    def test_release_by_id_is_scoped_and_idempotent(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 5), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            now = FAR_FUTURE
            ip_hash = rate_limit.hash_value("ip-a")
            user_hash = rate_limit.hash_value("admin@example.test")

            first = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)
            second = rate_limit.reserve_hard_limits(self.throttle, ip_hash, user_hash, now)

            rate_limit.release_hard_limits(self.throttle, first)
            # Releasing the same reservation twice is a safe no-op, not an error.
            rate_limit.release_hard_limits(self.throttle, first)

            bucket = rate_limit._bucket(rate_limit.IPUSER_WINDOW_MINUTES, now)
            key = rate_limit._ipuser_key(ip_hash, user_hash, bucket)
            doc = self.throttle.find_one({"_id": key})
            remaining_ids = {entry["id"] for entry in doc["reservations"]}
            self.assertEqual(remaining_ids, {second.ipuser_reservation_id})

            rate_limit.release_hard_limits(self.throttle, second)

    # -- privacy of stored keys -------------------------------------------

    def test_stored_keys_never_contain_plaintext_ip_or_username(self):
        now = FAR_FUTURE
        ip = "198.51.100.77"
        username = "someone-findable@example.test"
        reservation = rate_limit.reserve_hard_limits(
            self.throttle, rate_limit.hash_value(ip), rate_limit.hash_value(username), now
        )
        rate_limit.commit_failure(self.throttle, reservation, rate_limit.hash_value(username), now)

        for doc in self.throttle.find({}):
            blob = str(doc)
            self.assertNotIn(ip, blob)
            self.assertNotIn(username, blob)
            self.assertNotIn("someone-findable", blob)

    # -- no account-existence leak via the 429 response -------------------

    def test_429_response_is_identical_for_known_and_unknown_usernames(self):
        with patch.object(rate_limit, "IPUSER_MAX_FAILURES", 1), patch.object(
            rate_limit, "IP_MAX_FAILURES", 1000
        ):
            now = FAR_FUTURE
            with patch("app.rate_limit._now", return_value=now):
                self.assertEqual(self.login(password="wrong").status_code, 401)
                blocked_known = self.login(password="wrong")
                self.assertEqual(blocked_known.status_code, 429)

            self.client.cookies.clear()
            with patch("app.rate_limit._now", return_value=now):
                unknown_first = self.login(username="ghost@example.test", password="wrong")
                self.assertEqual(unknown_first.status_code, 401)
                blocked_unknown = self.login(username="ghost@example.test", password="wrong")
                self.assertEqual(blocked_unknown.status_code, 429)

            self.assertEqual(blocked_known.json(), blocked_unknown.json())
            self.assertEqual(
                blocked_known.headers["Retry-After"], blocked_unknown.headers["Retry-After"]
            )


if __name__ == "__main__":
    unittest.main()

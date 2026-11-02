"""Login rate limiting.

Three independent dimensions, keyed by HMAC-SHA256(secret, value) so a
client IP or username is never stored in plain text:

  * per IP            -- hard limit, blocks with 429 (credential stuffing
                          /spraying from one source).
  * per IP + username -- hard limit, blocks with 429 (focused brute force
                          against one account from one source).
  * per username alone -- soft limit. It NEVER blocks on its own: once a
                          username accumulates enough failures across many
                          IPs, further attempts against it (from anywhere)
                          get a small added delay, capped low. An attacker
                          spread across many IPs can slow the account down
                          a little; they can never lock the real owner out,
                          because this dimension has no reject path at all.

Hard limits use leased, uniquely-identified reservations rather than a
plain counter, so concurrent requests are bounded correctly (admission is
one atomic MongoDB document update, not a read followed by a write) and a
worker that crashes mid-request cannot hold a reservation forever -- its
lease simply stops counting once it expires, typically within seconds,
never within the full rate-limit window. See reserve_hard_limits() for the
atomic query/update and release_hard_limits()/commit_failure() for how a
reservation is retired.

All three dimensions use fixed time-window buckets: the key embeds
floor(now / window), so a window's document is always fresh (never carries
over stale state) and the TTL index on window_expires_at cleans old windows
up on its own.
"""

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

GENERIC_RATE_LIMIT_ERROR = "Too many login attempts. Please try again later."


def _positive_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise RuntimeError(f"{name} must be a positive integer.")
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


def _positive_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise RuntimeError(f"{name} must be a positive number.")
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive number.")
    return parsed


# A shared secret is required so the same IP/username always hashes to the
# same key on every worker and every instance -- the whole point of putting
# counters in MongoDB instead of process memory. Deliberately not fatal when
# unset (a fresh local checkout should still start), but a per-process random
# fallback only works correctly with a single worker: it is generated once
# here and logged loudly so a real multi-worker deployment notices before it
# ships silently-fragmented counters.
_RATE_LIMIT_SECRET_ENV = os.getenv("PMS_RATE_LIMIT_SECRET", "").strip()
if _RATE_LIMIT_SECRET_ENV:
    if len(_RATE_LIMIT_SECRET_ENV) < 32:
        raise RuntimeError(
            "PMS_RATE_LIMIT_SECRET must be at least 32 characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    RATE_LIMIT_SECRET = _RATE_LIMIT_SECRET_ENV
    RATE_LIMIT_SECRET_IS_GENERATED = False
else:
    RATE_LIMIT_SECRET = secrets.token_hex(32)
    RATE_LIMIT_SECRET_IS_GENERATED = True
    print(
        "WARNING: PMS_RATE_LIMIT_SECRET is not set. Using a random secret for this "
        "process only -- fine for a single local server, but every worker/instance "
        "needs the SAME value or their login-throttle counters will not agree with "
        "each other. Set PMS_RATE_LIMIT_SECRET in backend/.env before running more "
        "than one worker or instance."
    )

IP_MAX_FAILURES = _positive_int("PMS_LOGIN_IP_MAX_FAILURES", 20)
IP_WINDOW_MINUTES = _positive_int("PMS_LOGIN_IP_WINDOW_MINUTES", 15)
IPUSER_MAX_FAILURES = _positive_int("PMS_LOGIN_IPUSER_MAX_FAILURES", 5)
IPUSER_WINDOW_MINUTES = _positive_int("PMS_LOGIN_IPUSER_WINDOW_MINUTES", 15)
USER_SOFT_THRESHOLD = _positive_int("PMS_LOGIN_USER_SOFT_THRESHOLD", 15)
USER_WINDOW_MINUTES = _positive_int("PMS_LOGIN_USER_WINDOW_MINUTES", 60)
RESERVATION_LEASE_SECONDS = _positive_int("PMS_LOGIN_RESERVATION_LEASE_SECONDS", 10)
USER_SOFT_DELAY_STEP_SECONDS = _positive_float("PMS_LOGIN_USER_SOFT_DELAY_STEP_SECONDS", 0.25)
USER_SOFT_DELAY_CAP_SECONDS = _positive_float("PMS_LOGIN_USER_SOFT_DELAY_CAP_SECONDS", 4.0)

if IPUSER_MAX_FAILURES > IP_MAX_FAILURES:
    raise RuntimeError(
        "PMS_LOGIN_IPUSER_MAX_FAILURES cannot exceed PMS_LOGIN_IP_MAX_FAILURES "
        "-- the per-IP cap is meant to be the looser, catch-all one."
    )


def _now() -> datetime:
    """Overridden in tests to advance time without real sleeps."""
    return datetime.now(timezone.utc)


def hash_value(value: str) -> str:
    """HMAC rather than a bare hash: usernames and IPs are low-entropy, so a
    bare sha256 could be reversed by dictionary lookup if the collection is
    ever read directly. The secret makes that infeasible."""
    return hmac.new(
        RATE_LIMIT_SECRET.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def client_ip(request) -> str:
    """The ASGI-level TCP peer address only -- never a forwarding header.

    X-Forwarded-For / X-Real-IP are attacker-controlled unless a trusted
    proxy strips and rewrites them, and this deployment has no trusted-proxy
    configuration. Reading them here would let any client pick its own rate
    limit bucket.
    """
    return request.client.host if request.client else "unknown"


def _bucket(window_minutes: int, now: datetime) -> int:
    window_seconds = window_minutes * 60
    return int(now.timestamp() // window_seconds)


def _window_expiry(window_minutes: int, bucket: int) -> datetime:
    window_seconds = window_minutes * 60
    return datetime.fromtimestamp((bucket + 1) * window_seconds, tz=timezone.utc)


def _ip_key(ip_hash: str, bucket: int) -> str:
    return f"ip:{ip_hash}:{bucket}"


def _ipuser_key(ip_hash: str, user_hash: str, bucket: int) -> str:
    return f"ipuser:{ip_hash}:{user_hash}:{bucket}"


def _user_key(user_hash: str, bucket: int) -> str:
    return f"user:{user_hash}:{bucket}"


def _retry_after_seconds(window_expires_at: datetime, now: datetime) -> int:
    remaining = (window_expires_at - now).total_seconds()
    return max(1, int(remaining) + 1)


class RateLimited(Exception):
    """Raised when a hard limit (IP or IP+username) denies admission."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(GENERIC_RATE_LIMIT_ERROR)


@dataclass
class HardLimitReservation:
    """Both reservations held for one login attempt, so the caller has a
    single object to pass to release_hard_limits()/commit_failure()."""

    ip_key: str
    ip_reservation_id: str
    ipuser_key: str
    ipuser_reservation_id: str


def _ensure_window_doc(collection: Collection, key: str, window_expires_at: datetime) -> None:
    """First request in a window creates its document; every later one in
    the same window is a no-op. Safe under concurrent first requests: a
    DuplicateKeyError just means another worker's upsert won the race."""
    try:
        collection.update_one(
            {"_id": key},
            {
                "$setOnInsert": {
                    "fail_count": 0,
                    "reservations": [],
                    "window_expires_at": window_expires_at,
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        pass


def _prune_expired_reservations(collection: Collection, key: str, now: datetime) -> None:
    """Storage hygiene only -- correctness never depends on this running
    promptly, or at all, because the admission check below already ignores
    expired entries on its own. Safe to skip under load; cheap to run."""
    collection.update_one(
        {"_id": key}, {"$pull": {"reservations": {"lease_expires_at": {"$lte": now}}}}
    )


def _try_reserve(
    collection: Collection, key: str, max_failures: int, now: datetime
) -> Optional[str]:
    """Atomically admit one more attempt iff confirmed failures plus
    currently-live reservations are still under the cap, and if so reserve a
    uniquely-identified, leased slot for it. One MongoDB document operation:
    concurrent callers are serialized by the server, not by this process, so
    no read-then-write race can let more than max_failures through."""
    reservation_id = secrets.token_hex(8)
    lease_expires_at = now + timedelta(seconds=RESERVATION_LEASE_SECONDS)
    updated = collection.find_one_and_update(
        {
            "_id": key,
            "$expr": {
                "$lt": [
                    {
                        "$add": [
                            "$fail_count",
                            {
                                "$size": {
                                    "$filter": {
                                        "input": {"$ifNull": ["$reservations", []]},
                                        "as": "r",
                                        "cond": {"$gt": ["$$r.lease_expires_at", now]},
                                    }
                                }
                            },
                        ]
                    },
                    max_failures,
                ]
            },
        },
        {"$push": {"reservations": {"id": reservation_id, "lease_expires_at": lease_expires_at}}},
        return_document=ReturnDocument.AFTER,
    )
    return reservation_id if updated is not None else None


def reserve_hard_limits(collection: Collection, ip_hash: str, user_hash: str, now: datetime) -> HardLimitReservation:
    """Admit a login attempt against the per-IP and per-IP+username hard
    limits, or raise RateLimited with the correct Retry-After.

    Both reservations are released by the caller exactly once, on every
    outcome (success, failure, or exception) -- see release_hard_limits()
    and commit_failure().
    """
    ip_bucket = _bucket(IP_WINDOW_MINUTES, now)
    ip_key = _ip_key(ip_hash, ip_bucket)
    ip_window_expires_at = _window_expiry(IP_WINDOW_MINUTES, ip_bucket)

    _ensure_window_doc(collection, ip_key, ip_window_expires_at)
    _prune_expired_reservations(collection, ip_key, now)
    ip_reservation_id = _try_reserve(collection, ip_key, IP_MAX_FAILURES, now)
    if ip_reservation_id is None:
        raise RateLimited(_retry_after_seconds(ip_window_expires_at, now))

    ipuser_bucket = _bucket(IPUSER_WINDOW_MINUTES, now)
    ipuser_key = _ipuser_key(ip_hash, user_hash, ipuser_bucket)
    ipuser_window_expires_at = _window_expiry(IPUSER_WINDOW_MINUTES, ipuser_bucket)

    try:
        _ensure_window_doc(collection, ipuser_key, ipuser_window_expires_at)
        _prune_expired_reservations(collection, ipuser_key, now)
        ipuser_reservation_id = _try_reserve(collection, ipuser_key, IPUSER_MAX_FAILURES, now)
    except Exception:
        # Whatever went wrong, the IP-level reservation already taken above
        # must not leak -- the caller never gets a HardLimitReservation back
        # to release it, since this function never returns one.
        collection.update_one({"_id": ip_key}, {"$pull": {"reservations": {"id": ip_reservation_id}}})
        raise

    if ipuser_reservation_id is None:
        # This attempt is being rejected -- give back the IP-level slot it
        # will never use.
        collection.update_one({"_id": ip_key}, {"$pull": {"reservations": {"id": ip_reservation_id}}})
        raise RateLimited(_retry_after_seconds(ipuser_window_expires_at, now))

    return HardLimitReservation(ip_key, ip_reservation_id, ipuser_key, ipuser_reservation_id)


def release_hard_limits(collection: Collection, reservation: HardLimitReservation) -> None:
    """Login succeeded, or verification raised -- either way this attempt is
    not a confirmed failure. Release both reservations without touching
    fail_count: a correct login must never cost the account any of its own
    failure budget, and an exception is a server fault, not a bad guess."""
    collection.update_one(
        {"_id": reservation.ip_key},
        {"$pull": {"reservations": {"id": reservation.ip_reservation_id}}},
    )
    collection.update_one(
        {"_id": reservation.ipuser_key},
        {"$pull": {"reservations": {"id": reservation.ipuser_reservation_id}}},
    )


def commit_failure(
    collection: Collection,
    reservation: HardLimitReservation,
    user_hash: str,
    now: datetime,
) -> None:
    """Login failed -- convert both reservations into permanent failures,
    and record the same failure against the username-wide soft counter."""
    collection.update_one(
        {"_id": reservation.ip_key},
        {
            "$pull": {"reservations": {"id": reservation.ip_reservation_id}},
            "$inc": {"fail_count": 1},
        },
    )
    collection.update_one(
        {"_id": reservation.ipuser_key},
        {
            "$pull": {"reservations": {"id": reservation.ipuser_reservation_id}},
            "$inc": {"fail_count": 1},
        },
    )

    user_bucket = _bucket(USER_WINDOW_MINUTES, now)
    user_key = _user_key(user_hash, user_bucket)
    _ensure_window_doc(collection, user_key, _window_expiry(USER_WINDOW_MINUTES, user_bucket))
    collection.update_one({"_id": user_key}, {"$inc": {"fail_count": 1}})


def apply_user_soft_delay(collection: Collection, user_hash: str, now: datetime) -> float:
    """Never blocks. Once a username's failures (from any IP) pass the soft
    threshold, add a small, capped delay before this request continues --
    just enough to slow a distributed attack on one account, never enough to
    stop its real owner from logging in. Returns the delay actually slept,
    mainly so tests can assert on it without real sleeps."""
    user_bucket = _bucket(USER_WINDOW_MINUTES, now)
    user_key = _user_key(user_hash, user_bucket)
    doc = collection.find_one({"_id": user_key}, {"fail_count": 1})
    fail_count = doc["fail_count"] if doc else 0
    if fail_count <= USER_SOFT_THRESHOLD:
        return 0.0
    delay = min(
        USER_SOFT_DELAY_STEP_SECONDS * (fail_count - USER_SOFT_THRESHOLD),
        USER_SOFT_DELAY_CAP_SECONDS,
    )
    if delay > 0:
        time.sleep(delay)
    return delay

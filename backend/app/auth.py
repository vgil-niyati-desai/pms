"""Cookie-based authentication shared by the API and the login endpoints."""

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional, TypedDict
from urllib.parse import urlsplit

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from . import mongodb, rate_limit

router = APIRouter(prefix="/auth", tags=["authentication"])

Role = Literal["Admin", "HOD", "Normal User"]
VALID_ROLES = frozenset({"Admin", "HOD", "Normal User"})
COOKIE_NAME = "pms_session"
SESSION_HOURS = int(os.getenv("PMS_SESSION_HOURS", "8"))
if SESSION_HOURS < 1 or SESSION_HOURS > 168:
    raise RuntimeError("PMS_SESSION_HOURS must be between 1 and 168.")


def _setting_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


COOKIE_SECURE = _setting_bool("PMS_COOKIE_SECURE", True)
COOKIE_SAMESITE = os.getenv("PMS_COOKIE_SAMESITE", "strict").strip().lower()
if COOKIE_SAMESITE not in {"strict", "lax", "none"}:
    raise RuntimeError("PMS_COOKIE_SAMESITE must be strict, lax, or none.")
if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
    raise RuntimeError("PMS_COOKIE_SAMESITE=none requires PMS_COOKIE_SECURE=true.")

PASSWORD_ITERATIONS = 600_000
GENERIC_LOGIN_ERROR = "Invalid username or password."
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$MDAwMDAwMDAwMDAwMDAwMA=="
    "$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)

_ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("PMS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}
if COOKIE_SECURE and any(urlsplit(origin).scheme != "https" for origin in _ALLOWED_ORIGINS):
    raise RuntimeError("PMS_ALLOWED_ORIGINS must use HTTPS when PMS_COOKIE_SECURE=true.")


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class CreateAccountInput(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=12, max_length=1024)
    role: Role
    is_active: bool = True


class UpdateAccountInput(BaseModel):
    role: Optional[Role] = None
    is_active: Optional[bool] = None


class UserAccount(TypedDict):
    username: str
    password_hash: str
    role: str
    is_active: bool
    created_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalise_username(username: str) -> str:
    return username.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
        if iterations < PASSWORD_ITERATIONS or iterations > 1_000_000:
            return False
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _public_user(user: Dict[str, Any]) -> Dict[str, str]:
    role = user.get("role")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail="Account role is invalid.")
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "role": role,
    }


def require_roles(*roles: str):
    allowed = frozenset(roles)

    def dependency(user: Dict[str, str] = Depends(require_user)) -> Dict[str, str]:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="You do not have access to this operation.")
        return user

    return dependency


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )


def _check_login_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    parsed = urlsplit(origin)
    request_host = request.headers.get("host", "").lower()
    expected_scheme = "https" if COOKIE_SECURE else "http"
    if (
        origin
        and parsed.scheme == expected_scheme
        and parsed.netloc.lower() == request_host
    ):
        return
    if origin and origin in _ALLOWED_ORIGINS and (
        not COOKIE_SECURE or parsed.scheme == "https"
    ):
        return
    if (
        not COOKIE_SECURE
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
    ):
        host = parsed.port
        if host is not None and 5170 <= host <= 5179:
            return
    raise HTTPException(status_code=403, detail="Login request origin is not allowed.")


def require_user(
    request: Request,
    response: Response,
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
    users: Collection = Depends(mongodb.get_users_collection),
) -> Dict[str, str]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    now = _now()
    session = sessions.find_one(
        {"token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()}
    )
    expires_at = session.get("expires_at") if session else None
    if not isinstance(expires_at, datetime) or expires_at <= now:
        if session:
            sessions.delete_one({"_id": session["_id"]})
        _clear_session_cookie(response)
        raise HTTPException(status_code=401, detail="Authentication required.")

    user = users.find_one({"_id": session["user_id"]})
    if not user or not user.get("is_active", False):
        sessions.delete_one({"_id": session["_id"]})
        _clear_session_cookie(response)
        raise HTTPException(status_code=401, detail="Authentication required.")

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        session_csrf_token = session.get("csrf_token")
        if (
            not isinstance(session_csrf_token, str)
            or not x_csrf_token
            or not hmac.compare_digest(x_csrf_token, session_csrf_token)
        ):
            raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")

    return _public_user(user)


def _session_response(
    user: Dict[str, Any], csrf_token: str, expires_at: datetime
) -> Dict[str, Any]:
    return {
        "user": _public_user(user),
        "csrf_token": csrf_token,
        "expires_at": expires_at.isoformat(),
    }


@router.post("/login")
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    users: Collection = Depends(mongodb.get_users_collection),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
    throttle: Collection = Depends(mongodb.get_login_throttle_collection),
):
    _check_login_origin(request)
    username = normalise_username(payload.username)

    now = rate_limit._now()
    ip_hash = rate_limit.hash_value(rate_limit.client_ip(request))
    user_hash = rate_limit.hash_value(username)

    # Hard limits (per IP, per IP+username) are checked -- and their
    # reservations recorded -- for every attempted username, real or not,
    # so a 429's status, body, and Retry-After never differ by whether the
    # account exists.
    try:
        reservation = rate_limit.reserve_hard_limits(throttle, ip_hash, user_hash, now)
    except rate_limit.RateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=rate_limit.GENERIC_RATE_LIMIT_ERROR,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    outcome = "exception"
    try:
        # Soft, username-wide delay. Never rejects -- see app/rate_limit.py.
        # Inside this try/finally so an exception here is released exactly
        # like one raised during credential verification below.
        rate_limit.apply_user_soft_delay(throttle, user_hash, now)
        user = users.find_one({"username": username})
        password_hash = (
            user.get("password_hash", _DUMMY_PASSWORD_HASH)
            if user
            else _DUMMY_PASSWORD_HASH
        )
        password_matches = verify_password(payload.password, password_hash)
        login_ok = bool(user and password_matches and user.get("is_active", False))
        outcome = "success" if login_ok else "failure"
    finally:
        # Runs on success, on a confirmed failure, AND when the try block
        # above raises -- an exception leaves outcome at "exception", which
        # releases the reservation without charging it as a failed attempt,
        # the same as a success. See release_hard_limits()/commit_failure().
        if outcome == "failure":
            rate_limit.commit_failure(throttle, reservation, user_hash, now)
        else:
            rate_limit.release_hard_limits(throttle, reservation)

    if not login_ok:
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    _public_user(user)

    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    created_at = _now()
    expires_at = created_at + timedelta(hours=SESSION_HOURS)
    sessions.insert_one(
        {
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "csrf_token": csrf_token,
            "user_id": user["_id"],
            "created_at": created_at,
            "expires_at": expires_at,
        }
    )
    _set_session_cookie(response, token)
    return _session_response(user, csrf_token, expires_at)


@router.get("/me")
def current_session(
    request: Request,
    user: Dict[str, str] = Depends(require_user),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
):
    token = request.cookies[COOKIE_NAME]
    session = sessions.find_one(
        {"token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return {
        "user": user,
        "csrf_token": session["csrf_token"],
        "expires_at": session["expires_at"].isoformat(),
    }


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: Dict[str, str] = Depends(require_user),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        sessions.delete_one(
            {"token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()}
        )
    _clear_session_cookie(response)


def _user_object_id(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="User not found.")


_ACCOUNT_LOCK_ID = "admin-account-mutation-lock"


def _acquire_account_lock(sessions: Collection) -> str:
    """Serialize account changes across app workers without exposing a token."""
    try:
        sessions.update_one(
            {"_id": _ACCOUNT_LOCK_ID},
            {"$setOnInsert": {"lock_until": _now() - timedelta(seconds=1)}},
            upsert=True,
        )
    except DuplicateKeyError:
        # Another worker created the single lock record at the same time.
        pass

    token = secrets.token_urlsafe(24)
    now = _now()
    lock = sessions.find_one_and_update(
        {
            "_id": _ACCOUNT_LOCK_ID,
            "$or": [
                {"lock_until": {"$lte": now}},
                {"lock_until": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "lock_until": now + timedelta(minutes=2),
                "lock_token": token,
            }
        },
        return_document=True,
    )
    if lock is None:
        raise HTTPException(
            status_code=409,
            detail="Another account change is in progress. Please try again.",
        )
    return token


def _release_account_lock(sessions: Collection, token: str) -> None:
    sessions.update_one(
        {"_id": _ACCOUNT_LOCK_ID, "lock_token": token},
        {"$set": {"lock_until": _now()}, "$unset": {"lock_token": ""}},
    )


def _admin_count(users: Collection) -> int:
    return users.count_documents({"role": "Admin", "is_active": True})


@router.get("/users")
def list_users(
    _: Dict[str, str] = Depends(require_roles("Admin")),
    users: Collection = Depends(mongodb.get_users_collection),
):
    return [
        {
            "id": str(user["_id"]),
            "username": user.get("username", ""),
            "role": user.get("role", ""),
            "is_active": bool(user.get("is_active", False)),
            "created_at": user.get("created_at"),
        }
        for user in users.find({}, {"username": 1, "role": 1, "is_active": 1, "created_at": 1})
        .sort([("username", 1), ("_id", 1)])
    ]


@router.post("/users", status_code=201)
def create_account(
    payload: CreateAccountInput,
    _: Dict[str, str] = Depends(require_roles("Admin")),
    users: Collection = Depends(mongodb.get_users_collection),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
):
    token = _acquire_account_lock(sessions)
    try:
        try:
            user_id = create_user(
                payload.username,
                payload.password,
                users,
                role=payload.role,
                is_active=payload.is_active,
            )
        except DuplicateKeyError:
            raise HTTPException(status_code=409, detail="That username already exists.")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        user = users.find_one({"_id": user_id})
        return {
            "id": str(user_id),
            "username": user["username"],
            "role": user["role"],
            "is_active": user["is_active"],
            "created_at": user["created_at"],
        }
    finally:
        _release_account_lock(sessions, token)


@router.patch("/users/{user_id}")
def update_account(
    user_id: str,
    payload: UpdateAccountInput,
    _: Dict[str, str] = Depends(require_roles("Admin")),
    users: Collection = Depends(mongodb.get_users_collection),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide a role or active-state change.")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="Role and active state cannot be null.")

    token = _acquire_account_lock(sessions)
    try:
        oid = _user_object_id(user_id)
        current = users.find_one({"_id": oid})
        if not current:
            raise HTTPException(status_code=404, detail="User not found.")

        updated_role = changes.get("role", current.get("role"))
        updated_active = changes.get("is_active", current.get("is_active", False))
        is_active_admin = current.get("role") == "Admin" and current.get("is_active") is True
        remains_active_admin = updated_role == "Admin" and updated_active is True
        if is_active_admin and not remains_active_admin and _admin_count(users) <= 1:
            raise HTTPException(
                status_code=409,
                detail="The last active Admin cannot be disabled or demoted.",
            )

        updated = users.find_one_and_update(
            {"_id": oid},
            {"$set": changes},
            return_document=True,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="User not found.")

        revoked_sessions = 0
        if "role" in changes or changes.get("is_active") is False:
            revoked_sessions = sessions.delete_many({"user_id": oid}).deleted_count

        return {
            "id": str(updated["_id"]),
            "username": updated["username"],
            "role": updated["role"],
            "is_active": updated["is_active"],
            "created_at": updated["created_at"],
            "revoked_sessions": revoked_sessions,
        }
    finally:
        _release_account_lock(sessions, token)


@router.delete("/users/{user_id}/sessions", status_code=204)
def revoke_user_sessions(
    user_id: str,
    _: Dict[str, str] = Depends(require_roles("Admin")),
    users: Collection = Depends(mongodb.get_users_collection),
    sessions: Collection = Depends(mongodb.get_sessions_collection),
):
    oid = _user_object_id(user_id)
    if not users.find_one({"_id": oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="User not found.")
    sessions.delete_many({"user_id": oid})


def create_user(
    username: str,
    password: str,
    users: Optional[Collection] = None,
    role: str = "Admin",
    is_active: bool = True,
) -> ObjectId:
    """Create an account after validating its role and password constraints."""
    normalized = normalise_username(username)
    if not normalized or len(normalized) > 254:
        raise ValueError("Username must contain between 1 and 254 characters.")
    if role not in VALID_ROLES:
        raise ValueError("Role must be Admin, HOD, or Normal User.")
    if len(password) > 1024:
        raise ValueError("Password cannot exceed 1024 characters.")
    collection = users if users is not None else mongodb.get_users_collection()
    account: UserAccount = {
        "username": normalized,
        "password_hash": hash_password(password),
        "role": role,
        "is_active": is_active,
        "created_at": _now(),
    }
    result = collection.insert_one(account)
    return result.inserted_id

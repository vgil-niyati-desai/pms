"""List a small sample of Google Drive metadata without reading file contents."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"
CLIENT_SECRETS = Path(
    r"C:\Users\HP\Downloads\client_secret_519613123942-l1no88nf9vg68akslms6iujav1k4kfjh.apps.googleusercontent.com.json"
)


def token_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".config"
    path = (base / "PMS" / "GoogleDrive" / "token.json").resolve()
    repository = Path(__file__).resolve().parents[1]
    if path.is_relative_to(repository):
        raise RuntimeError("OAuth token location must be outside the repository.")
    return path


def save_token(credentials: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
        token_file.write(credentials.to_json())
    if os.name != "nt":
        os.chmod(path, 0o600)


def actual_granted_scopes(credentials: Credentials) -> set[str]:
    """Read granted scopes from Google's token-info endpoint, never local claims."""
    if not credentials.token:
        return set()
    request = UrlRequest(
        TOKEN_INFO_URL,
        data=urlencode({"access_token": credentials.token}).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            token_info = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        raise RuntimeError(
            "Google token verification failed; token details were suppressed."
        ) from None
    scope_value = token_info.get("scope", "")
    if not isinstance(scope_value, str):
        raise RuntimeError("Google returned an invalid token scope response.")

    client_config = json.loads(CLIENT_SECRETS.read_text(encoding="utf-8"))
    oauth_client = client_config.get("installed", client_config.get("web", {}))
    expected_client_id = oauth_client.get("client_id")
    if expected_client_id and not (
        token_info.get("aud") == expected_client_id
        or token_info.get("azp") == expected_client_id
    ):
        raise RuntimeError("Google token belongs to a different OAuth client.")
    return set(scope_value.split())


def authenticate() -> Credentials:
    path = token_path()
    credentials = None
    if path.exists():
        credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_token(credentials, path)
    granted_scopes = (
        actual_granted_scopes(credentials)
        if credentials and credentials.valid
        else set()
    )
    if not credentials or not credentials.valid or not set(SCOPES).issubset(
        granted_scopes
    ):
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRETS), SCOPES
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            prompt="consent select_account",
            success_message="Google Drive authorization complete. You may close this tab.",
        )
        save_token(credentials, path)
        granted_scopes = actual_granted_scopes(credentials)
    if not set(SCOPES).issubset(granted_scopes):
        path.unlink(missing_ok=True)
        raise RuntimeError(
            "Google did not grant every requested Drive scope; token removed."
        )
    return credentials


def list_drive_items(service: Any, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while len(items) < limit:
        response = (
            service.files()
            .list(
                q="trashed = false",
                pageSize=min(limit - len(items), 1000),
                pageToken=page_token,
                orderBy="name",
                spaces="drive",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="nextPageToken,files(id,name,mimeType,parents)",
            )
            .execute()
        )
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Authenticate with Google Drive and list metadata only."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="maximum number of Drive items to list (1-100; default: 5)",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100.")

    try:
        credentials = authenticate()
        granted_scopes = actual_granted_scopes(credentials)
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        items = list_drive_items(service, args.limit)
    except Exception:
        print(
            "Google Drive authentication or listing failed. Check the OAuth client, "
            "browser consent, network, and Drive API access. Sensitive error details "
            "were suppressed.",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {"granted_scopes": sorted(granted_scopes), "count": len(items), "items": items},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

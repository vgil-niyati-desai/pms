"""One-process deployment entry point: the built frontend and the API together.

    uvicorn pms_server:app --host 127.0.0.1 --port 8650

run from backend/. The browser sees a single origin:

    /api/...   the FastAPI application in app/main.py, with the "/api" prefix
               removed before it is routed (the same arrangement a reverse proxy
               would give; the frontend is built with VITE_API_BASE_URL=/api)
    anything   the frontend's static files (PMS_FRONTEND_DIST); a path that is
    else       not a file falls back to index.html so a browser refresh on a
               client-side route such as /employees still loads the app

Why a wrapper rather than mounting the app: the application's own startup
(index creation, hash backfill) must run, and a mounted sub-application never
receives the lifespan events. Here lifespan goes straight to the application.

Nothing in app/ is changed by this file, and local development
(`uvicorn app.main:app` + `npm run dev`) does not use it.

Settings:
    PMS_FRONTEND_DIST   absolute path of the built frontend (contains index.html).
                        Required.
"""

import os
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

from app.main import app as api

API_PREFIX = "/api"


def _frontend_dir() -> str:
    path = os.getenv("PMS_FRONTEND_DIST", "").strip()
    if not path or not os.path.isabs(path):
        raise RuntimeError("PMS_FRONTEND_DIST must be set to an absolute path.")
    if not os.path.isfile(os.path.join(path, "index.html")):
        raise RuntimeError("PMS_FRONTEND_DIST has no index.html: build the frontend first.")
    return path


class SinglePageFiles(StaticFiles):
    """Static files, with index.html for a route that is not a file.

    A missing path that looks like a file (it has an extension) is still a
    404, so a broken asset link is reported as one instead of the browser
    being handed HTML in place of a script.
    """

    async def __call__(self, scope, receive, send):
        # Used as a bare ASGI app (no Starlette router around it), so an
        # HTTPException from the file lookup has nothing to turn it into a
        # response: answer it here rather than let it become a 500.
        try:
            await super().__call__(scope, receive, send)
        except HTTPException as exc:
            await PlainTextResponse(exc.detail, status_code=exc.status_code)(scope, receive, send)

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


static = SinglePageFiles(directory=_frontend_dir(), html=True)


def _is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def _prefix_redirects(send):
    """Put the prefix back on a redirect the application issued.

    FastAPI answers /documents with a 307 to /documents/ (trailing-slash
    redirect) built from the stripped path, so the browser would be sent to
    /documents/ -- a frontend page, not the API. Only a redirect to this same
    host that lacks the prefix is touched.
    """

    async def wrapped(message):
        if message["type"] == "http.response.start" and message["status"] in (301, 302, 307, 308):
            headers = []
            for name, value in message["headers"]:
                if name.lower() == b"location":
                    parts = urlsplit(value.decode("latin-1"))
                    if parts.path.startswith("/") and not _is_api_path(parts.path):
                        parts = parts._replace(path=API_PREFIX + parts.path)
                        value = parts.geturl().encode("latin-1")
                headers.append((name, value))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await api(scope, receive, send)
        return
    if scope["type"] in ("http", "websocket") and _is_api_path(scope["path"]):
        inner = dict(scope)
        inner["path"] = scope["path"][len(API_PREFIX):] or "/"
        raw_path = scope.get("raw_path")
        if raw_path and raw_path.startswith(API_PREFIX.encode()):
            inner["raw_path"] = raw_path[len(API_PREFIX):] or b"/"
        # root_path makes the application build absolute links (a trailing-slash
        # redirect, /docs's openapi.json) with the prefix the browser used.
        inner["root_path"] = scope.get("root_path", "") + API_PREFIX
        await api(inner, receive, _prefix_redirects(send))
        return
    await static(scope, receive, send)

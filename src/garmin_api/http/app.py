"""The FastAPI application.

Binds to localhost by default. Garmin credentials never come through here:
the service reads tokens the CLI wrote, and answers 401 with instructions if
there are none.

Every route's ``operation_id`` and docstring become an MCP tool's name and
description, so they are written to be read by a model as well as a person.
"""

from __future__ import annotations

import hmac
import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import __version__, auth
from ..errors import GarminApiError
from .routers import activities, exercises, health, sessions, workouts

API_KEY_ENV = "GARMIN_API_KEY"
# Extra host names to answer to, comma-separated, e.g. a LAN name when bound
# beyond localhost with a key set.
HOSTS_ENV = "GARMIN_API_HOSTS"
# "testserver" is the host name FastAPI's TestClient sends.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
OPEN_PATHS = frozenset({"/healthz", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


def _extra_hosts() -> frozenset[str]:
    raw = os.getenv(HOSTS_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def create_app() -> FastAPI:
    app = FastAPI(
        title="Garmin Connect API",
        version=__version__,
        description=(
            "Read and write Garmin Connect: activities with per-set strength detail, "
            "daily health data, planned workouts, and completed sessions uploaded as "
            "FIT files. Authentication is by tokens written by the `garmin login` CLI."
        ),
    )

    @app.exception_handler(GarminApiError)
    async def _garmin_error(_: Request, exc: GarminApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.as_dict())

    @app.middleware("http")
    async def _guard(request: Request, call_next: Any) -> Any:
        """Keep other web pages, and other hosts' names, away from the account.

        The service can write to Garmin, and a page open in any browser on
        this machine can send requests to localhost. Three checks:

        * A write that carries an ``Origin`` header came from a web page, and
          is refused unless that page is this service itself.
        * A request for a host name other than the ones the service answers to
          is refused, which defeats DNS rebinding.
        * When GARMIN_API_KEY is set, every request outside the docs and the
          health check must present it, compared in constant time.
        """
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        allowed_hosts = LOCAL_HOSTS | _extra_hosts()
        if host and host not in allowed_hosts:
            return JSONResponse(status_code=403, content={"error": "Forbidden host"})

        origin = request.headers.get("origin")
        if origin and request.method not in SAFE_METHODS:
            origin_host = (urlsplit(origin).hostname or "").lower()
            if origin_host not in allowed_hosts:
                return JSONResponse(
                    status_code=403, content={"error": "Cross-origin write refused"}
                )

        expected = os.getenv(API_KEY_ENV)
        if expected and request.url.path not in OPEN_PATHS:
            header = request.headers.get("authorization") or ""
            scheme, _, supplied = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.strip().encode(), expected.encode()
            ):
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return await call_next(request)

    @app.get("/healthz", operation_id="healthz", tags=["service"])
    def healthz() -> dict[str, Any]:
        """Whether the service is up and whether it holds Garmin tokens.

        Does not call Garmin, so it is safe to poll.
        """
        return {
            "status": "ok",
            "version": __version__,
            "authenticated": auth.has_tokens(),
            "legacy_tokens_only": auth.legacy_tokens(),
            "token_dir": str(auth.token_dir()),
            "token_age_days": round(auth.token_age_days() or 0, 1) if auth.has_tokens() else None,
        }

    for router in (
        activities.router,
        health.router,
        workouts.router,
        sessions.router,
        exercises.router,
    ):
        app.include_router(router, prefix="/v1")

    return app


app = create_app()

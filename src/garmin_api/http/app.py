"""The FastAPI application.

Binds to localhost by default. Garmin credentials never come through here:
the service reads tokens the CLI wrote, and answers 401 with instructions if
there are none.

Every route's ``operation_id`` and docstring become an MCP tool's name and
description, so they are written to be read by a model as well as a person.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .. import __version__, auth
from ..errors import GarminApiError
from .routers import activities, exercises, health, sessions, workouts

API_KEY_ENV = "GARMIN_API_KEY"
# Extra host names to answer to, comma-separated, e.g. a LAN name when bound
# beyond localhost with a key set.
HOSTS_ENV = "GARMIN_API_HOSTS"
# Never a name anyone else could make resolve here: "testserver", which
# FastAPI's TestClient sends, once stood in this list and let a rebound name
# reach every write. Tests name it through GARMIN_API_HOSTS instead.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
OPEN_PATHS = frozenset({"/healthz", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


logger = logging.getLogger(__name__)


def _extra_hosts() -> frozenset[str]:
    raw = os.getenv(HOSTS_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def refusal(
    headers: Mapping[str, str], method: str, path: str, scheme: str = "http"
) -> JSONResponse | None:
    """Why a request must be refused, or None. Keeps other web pages, and other
    hosts' names, away from the account.

    The service can write to Garmin, and a page open in any browser on this
    machine can send requests to localhost. Three checks:

    * A request for a host name other than the ones the service answers to is
      refused, which defeats DNS rebinding.
    * A write that carries an ``Origin`` header came from a web page, and is
      refused unless that page is this service itself: same host and port. A
      page served by anything else on localhost is another site.
    * When GARMIN_API_KEY is set, every request outside the docs and the
      health check must present it, compared in constant time.
    """
    host_header = (headers.get("host") or "").strip().lower()
    origin = headers.get("origin")
    try:
        host, host_port = _host_and_port(host_header, "")
        if host_port is None and host in LOCAL_HOSTS:
            # A local name without a port is this request's own scheme's
            # default: an https page is not this plain http service.
            host_port = {"http": 80, "https": 443}.get(scheme)
        site = urlsplit(origin.strip()) if origin is not None else None
        origin_at = (
            _host_and_port(site.netloc.lower(), site.scheme)
            if site is not None and site.netloc
            else None
        )
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Malformed Host or Origin"})
    if host_header and host not in LOCAL_HOSTS | _extra_hosts():
        return JSONResponse(status_code=403, content={"error": "Forbidden host"})

    # A browser says where a request comes from. Another site's page cannot
    # read the answer to a GET, but every GET still spends the account's
    # Garmin requests: an image tag on any page could keep it rate limited.
    if (headers.get("sec-fetch-site") or "").lower() in ("cross-site", "same-site"):
        return JSONResponse(status_code=403, content={"error": "Cross-site request refused"})

    # Same host and port. A Host without a port means the Origin scheme's
    # default port, not whatever port the Origin names.
    # A name from GARMIN_API_HOSTS without a port is usually behind a proxy
    # that ends TLS, where this service sees http however the page was
    # served; the Origin scheme's default port is the one to expect.
    default = {"http": 80, "https": 443}.get(site.scheme) if site is not None else None
    expected_port = host_port if host_port is not None else default
    foreign = origin is not None and (origin_at is None or origin_at != (host, expected_port))
    if foreign:
        return JSONResponse(status_code=403, content={"error": "Cross-origin request refused"})

    expected = os.getenv(API_KEY_ENV)
    if expected and path not in OPEN_PATHS:
        scheme, _, supplied = (headers.get("authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.strip().encode(), expected.encode()
        ):
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return None


def _host_and_port(netloc: str, scheme: str) -> tuple[str, int | None]:
    """A host and its port from "host[:port]"; raises ValueError when malformed."""
    if not netloc:
        return "", None
    parts = urlsplit("//" + netloc)
    port = parts.port  # raises ValueError for a port that is not a number
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return (parts.hostname or "").rstrip("."), port


class RequestGuard:
    """ASGI middleware applying :func:`refusal`, for this app and the MCP HTTP server."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            if b"%2f" in (scope.get("raw_path") or b"").lower():
                # The server decodes %2F before routing, so an id such as
                # "schedule%2F1" would reach a different route; no route here
                # takes a slash inside a value.
                slash = JSONResponse(status_code=400, content={"error": "Encoded slash in path"})
                await slash(scope, receive, send)
                return
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
            }
            refused = refusal(
                headers,
                scope.get("method", "GET"),
                scope.get("path", ""),
                scope.get("scheme", "http"),
            )
            if refused is not None:
                await refused(scope, receive, send)
                return
        await self.app(scope, receive, send)


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

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own handler echoes each bad input back, and an infinity or
        # NaN cannot be written as JSON: the refusal itself became a 500.
        problems = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg"), "type": error.get("type")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": problems})

    @app.exception_handler(ValidationError)
    async def _unexpected_shape(_: Request, exc: ValidationError) -> JSONResponse:
        # Request bodies and parameters are validated before a route runs and
        # answer 422; a ValidationError inside one comes from Garmin's reply.
        return JSONResponse(
            status_code=502,
            content={
                "error": "GarminUnavailable",
                "message": "Garmin answered in a shape this package did not expect.",
                "detail": str(exc),
            },
        )

    @app.middleware("http")
    async def _no_bare_500(request: Request, call_next: Any) -> Any:
        """Answer any other failure with a JSON error rather than a raised exception.

        The MCP tools call this app in-process, where an exception that
        escapes the app would be reported as the model's own invalid input.
        """
        try:
            response = await call_next(request)
            # Nothing here is meant to be framed; the docs page least of all.
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            return response
        except Exception as exc:  # noqa: BLE001 -- the last line of defence
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"error": type(exc).__name__, "message": str(exc), "detail": None},
            )

    app.add_middleware(RequestGuard)

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

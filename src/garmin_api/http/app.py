"""The FastAPI application.

Binds to localhost by default. Garmin credentials never come through here:
the service reads tokens the CLI wrote, and answers 401 with instructions if
there are none.

Every route's ``operation_id`` and docstring become an MCP tool's name and
description, so they are written to be read by a model as well as a person.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import __version__, auth
from ..errors import GarminApiError
from .routers import activities, exercises, health, sessions, workouts

API_KEY_ENV = "GARMIN_API_KEY"


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
    async def _api_key(request: Request, call_next: Any) -> Any:
        """Require a bearer token when one is configured.

        Unset by default, which is right for a service on localhost. Set
        GARMIN_API_KEY before binding to anything else.
        """
        expected = os.getenv(API_KEY_ENV)
        if expected and request.url.path not in {"/healthz", "/docs", "/openapi.json"}:
            supplied = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
            if supplied != expected:
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

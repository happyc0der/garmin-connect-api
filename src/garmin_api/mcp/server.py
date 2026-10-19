"""The MCP server, generated from the HTTP app.

Deriving the tools from the FastAPI application rather than writing them by
hand means the API and the tools cannot drift: one route, one tool, one
description. What is added here is a small number of tools that exist because
a model asking a question wants a different shape from a REST client, and
guidance a model needs but an HTTP caller does not.
"""

from __future__ import annotations

import functools
import os
from datetime import date, timedelta
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from .. import __version__, auth, catalogue
from ..errors import GarminUnavailable, InvalidRequest, NotAuthenticated, RateLimited
from ..http.app import API_KEY_ENV, create_app
from ..services import activities as activities_service

INSTRUCTIONS = """
Read and write a Garmin Connect account.

Before writing anything:

* Exercises must be named with Garmin's own category and exercise_name pair,
  for example BENCH_PRESS and BARBELL_BENCH_PRESS. find_exercise gives them
  (the variant under "exercise", which the write tools also accept); an
  unknown name is rejected.
* A workout and a session are different. A workout is a plan to follow
  later: creating one saves it, and schedule_date puts it on the calendar. A
  session is something already done: logging one adds an activity to the
  history. Completed sessions are activities, not workouts.
* A logged session may be shared automatically to Strava or another app the
  account is connected to, where followers can see it, and deleting it from
  Garmin does not delete it there. Say so before logging one.
* To correct a logged session, use edit_activity_sets. Logging it again with
  changed sets creates a second activity.

If a call reports that Garmin is rate limiting the account, stop. Retrying
can lock the account out for a day or more.
""".strip()


# Paths answered entirely from the bundled catalogue or the service itself:
# they never reach Garmin, so they are not "open world".
LOCAL_PATHS = ("/healthz", "/v1/exercises/")
# Overwrites that do something different when repeated: deleting set 2 again
# deletes what was set 3.
NOT_IDEMPOTENT = frozenset({"edit_activity_sets"})


def _annotate(route: Any, component: Any) -> None:
    """Tell the client what each generated tool does to the account.

    Derived from the HTTP method, which is what the API already promises: GET
    reads and changes nothing; DELETE removes data; PATCH overwrites it (a
    rename loses the old name); POST adds something new. Clients use these to
    decide what may run without asking.
    """
    method = str(getattr(route, "method", "GET")).upper()
    path = str(getattr(route, "path", ""))
    reaches_garmin = not path.startswith(LOCAL_PATHS)
    if method == "GET":
        hints = ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=reaches_garmin
        )
    elif method in ("DELETE", "PATCH", "PUT"):
        hints = ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=str(getattr(route, "operation_id", "")) not in NOT_IDEMPOTENT,
            open_world_hint=True,
        )
    else:
        hints = ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
    hints.title = _title(str(getattr(route, "operation_id", "") or getattr(component, "name", "")))
    if hasattr(component, "annotations"):
        component.annotations = hints


ACRONYMS = {"hrv": "HRV", "fit": "FIT", "dto": "DTO", "id": "ID"}
TITLES = {"healthz": "Service health"}


def _title(operation_id: str) -> str:
    """A readable title from an operation id: delete_activity -> "Delete activity"."""
    if operation_id in TITLES:
        return TITLES[operation_id]
    words = [ACRONYMS.get(w, w) for w in operation_id.split("_") if w]
    if not words:
        return operation_id
    first = words[0] if words[0].isupper() else words[0].capitalize()
    return " ".join([first, *words[1:]]).replace("weigh in", "weigh-in")


async def _drop_origin(request: Any) -> None:
    """Keep an MCP client's Origin off the in-process call to the API.

    FastMCP copies the client's headers onto it. Over HTTP the guard in front
    of the MCP endpoint has already judged that Origin; the API inside would
    compare it with its own host, 127.0.0.1 with no port, and refuse writes.
    """
    if "origin" in request.headers:
        del request.headers["origin"]


def create_mcp() -> FastMCP:
    app = create_app()

    # The generated tools call the app in-process; when the app requires a key,
    # those calls must carry it too, or every generated tool answers 401.
    # The base URL is cosmetic -- the transport is in-process ASGI and never
    # touches a network -- but FastMCP's default host, "fastapi", is one the
    # app's host guard rightly refuses.
    key = os.getenv(API_KEY_ENV)
    client_kwargs: dict[str, Any] = {
        "base_url": "http://127.0.0.1",
        "event_hooks": {"request": [_drop_origin]},
    }
    if key:
        client_kwargs["headers"] = {"Authorization": f"Bearer {key}"}

    mcp = FastMCP.from_fastapi(
        app=app,
        httpx_client_kwargs=client_kwargs,
        mcp_component_fn=_annotate,
        name="garmin",
        instructions=INSTRUCTIONS,
        route_maps=[
            # The exercise catalogue is reference data that never changes
            # during a session, so it is a resource rather than a tool.
            RouteMap(
                methods=["GET"], pattern=r"^/v1/exercises/categories$", mcp_type=MCPType.RESOURCE
            ),
            # Sample-level detail and raw file downloads are hundreds of
            # kilobytes of numbers; they would flood a context window and help
            # nobody. Excluded from the tool list, still on the HTTP API.
            RouteMap(
                methods=["GET"],
                pattern=r"^/v1/activities/\{activity_id\}/splits$",
                mcp_type=MCPType.EXCLUDE,
            ),
            RouteMap(
                methods=["GET"],
                pattern=r"^/v1/activities/\{activity_id\}/file$",
                mcp_type=MCPType.EXCLUDE,
            ),
            RouteMap(
                methods=["POST"],
                pattern=r"^/v1/sessions/strength/encode$",
                mcp_type=MCPType.EXCLUDE,
            ),
            RouteMap(
                methods=["POST"],
                pattern=r"^/v1/sessions/strength/decode$",
                mcp_type=MCPType.EXCLUDE,
            ),
            RouteMap(methods=["POST"], pattern=r"^/v1/sessions/upload$", mcp_type=MCPType.EXCLUDE),
        ],
    )

    _add_convenience_tools(mcp)
    _guard_http(mcp)
    return mcp


def _guard_http(mcp: FastMCP) -> None:
    """Serve MCP over HTTP behind the same guard as the HTTP API.

    Over HTTP the tools are as reachable as the API's routes, and they write
    through an in-process client that already holds the key, so the guard has
    to sit in front of the MCP endpoint itself. Wrapping ``http_app`` covers
    ``run(transport="http")`` too, which builds its app through it.
    """
    from starlette.middleware import Middleware

    from ..http.app import RequestGuard

    unguarded = mcp.http_app

    def http_app(
        path: str | None = None, *, middleware: list[Any] | None = None, **kwargs: Any
    ) -> Any:
        guarded = [Middleware(RequestGuard), *(middleware or [])]
        return unguarded(path, middleware=guarded, **kwargs)

    mcp.http_app = http_app  # type: ignore[method-assign,assignment]


def _garmin_shaped(fn: Any) -> Any:
    """Report a hand-written tool's failures as the generated ones are.

    These tools call the services directly, so an oddly shaped Garmin reply
    never reaches the HTTP app's handler, and FastMCP would report it as the
    model's own invalid arguments. A date too far out is the caller's error.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ValidationError as exc:
            raise GarminUnavailable(
                "Garmin answered in a shape this package did not expect.", detail=str(exc)
            ) from exc
        except OverflowError as exc:
            raise InvalidRequest("A date is out of range.", detail=str(exc)) from exc

    return wrapper


def _add_convenience_tools(mcp: FastMCP) -> None:
    """Tools that answer a question rather than mirror an endpoint."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Garmin status", read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def garmin_status() -> dict[str, Any]:
        """Check whether this server holds a Garmin login.

        Call this first if anything fails. It reports whether tokens exist and
        how old they are, without calling Garmin, so tokens Garmin has since
        revoked still show as authenticated; any other call reveals that.
        """
        return {
            "version": __version__,
            "authenticated": auth.has_tokens(),
            "token_dir": str(auth.token_dir()),
            "token_age_days": round(auth.token_age_days() or 0, 1) if auth.has_tokens() else None,
            "hint": None if auth.has_tokens() else "Run `garmin login` in a terminal.",
        }

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Find exercise", read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def find_exercise(
        query: str, limit: Annotated[int, Field(ge=1, le=100)] = 10
    ) -> list[dict[str, str]]:
        """Find Garmin's category and exercise_name for an exercise.

        Always use this before creating a workout or logging a session with an
        exercise whose Garmin name is not already known.
        """
        return [e._asdict() for e in catalogue.search(query, limit=limit)]

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Recent strength sessions",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        )
    )
    @_garmin_shaped
    def recent_strength_sessions(
        count: Annotated[int, Field(ge=1, le=25)] = 5, include_sets: bool = True
    ) -> list[dict[str, Any]]:
        """Recent completed strength sessions (activities), with their sets.

        One call instead of listing activities and then fetching each one's
        sets. Includes the total volume, which is the sum of reps times weight
        over the working sets.
        """
        found = activities_service.list_activities(
            limit=max(1, min(count, 25)), activity_type=activities_service.STRENGTH
        )
        results: list[dict[str, Any]] = []
        for activity in found:
            entry: dict[str, Any] = {
                "activity_id": activity.activity_id,
                "name": activity.name,
                "start_time": activity.start_time.isoformat() if activity.start_time else None,
                "duration_s": activity.duration_s,
            }
            if include_sets:
                try:
                    sets = activities_service.get_sets(activity.activity_id)
                    entry["total_volume_kg"] = sets.total_volume_kg
                    entry["sets"] = [
                        {
                            "exercise": s.exercise_name or s.exercise_category,
                            "category": s.exercise_category,
                            "reps": s.reps,
                            "weight_kg": s.weight_kg,
                        }
                        for s in sets.sets
                    ]
                except (RateLimited, NotAuthenticated):
                    # Stop: carrying on would fire more calls at an account
                    # Garmin is already throttling.
                    raise
                except Exception as exc:  # noqa: BLE001 -- one bad activity must not sink the list
                    entry["sets_error"] = str(exc)
            results.append(entry)
        return results

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Week summary", read_only_hint=True, idempotent_hint=True, open_world_hint=True
        )
    )
    @_garmin_shaped
    def week_summary(end_day: date | None = None) -> dict[str, Any]:
        """Seven days of training and health in one answer.

        Covers the week ending on end_day, or today. Gives each day's steps,
        resting heart rate, sleep and training readiness, plus the activities
        in that window. A night's sleep belongs to the day it ended on.
        """
        # "Today" in the Garmin profile's timezone, not the machine's or UTC's:
        # after 8pm in New York, UTC is already tomorrow.
        last = end_day or auth.call("today")
        first = last - timedelta(days=6)

        from ..services import health as health_service

        days = []
        for offset in range(7):
            current = first + timedelta(days=offset)
            # Only the parts this answer uses: three requests a day, not five.
            summary = health_service.day(current, parts={"stats", "sleep", "readiness"})
            days.append(
                {
                    "day": current.isoformat(),
                    "steps": summary.steps,
                    "resting_hr": summary.resting_hr,
                    "sleep_hours": round(summary.sleep.total_sleep_s / 3600, 1)
                    if summary.sleep and summary.sleep.total_sleep_s
                    else None,
                    "training_readiness": summary.training_readiness,
                }
            )

        sessions = activities_service.list_activities(limit=50, start_date=first, end_date=last)
        return {
            "from": first.isoformat(),
            "to": last.isoformat(),
            "days": days,
            "activities": [
                {
                    "activity_id": a.activity_id,
                    "name": a.name,
                    "type": a.activity_type.type_key if a.activity_type else None,
                    "start_time": a.start_time.isoformat() if a.start_time else None,
                    "duration_s": a.duration_s,
                }
                for a in sessions
            ],
        }

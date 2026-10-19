"""The MCP server, generated from the HTTP app.

Deriving the tools from the FastAPI application rather than writing them by
hand means the API and the tools cannot drift: one route, one tool, one
description. What is added here is a small number of tools that exist because
a model asking a question wants a different shape from a REST client, and
guidance a model needs but an HTTP caller does not.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

from .. import __version__, auth, catalogue
from ..errors import NotAuthenticated, RateLimited
from ..http.app import API_KEY_ENV, create_app
from ..services import activities as activities_service

INSTRUCTIONS = """
Read and write a Garmin Connect account.

Two things worth knowing before writing anything:

* Exercises must be named with Garmin's own category and exercise_name pair,
  for example BENCH_PRESS and BARBELL_BENCH_PRESS. Search the exercise
  catalogue first; an unknown name is rejected.
* A workout and a session are different. Creating a strength workout puts a
  plan on the calendar to follow later. Logging a strength session records
  something already done, and it appears in the history as an activity.

If a call reports that Garmin is rate limiting the account, stop. Retrying
can lock the account out for a day or more.
""".strip()


def create_mcp() -> FastMCP:
    app = create_app()

    # The generated tools call the app in-process; when the app requires a key,
    # those calls must carry it too, or every generated tool answers 401.
    # The base URL is cosmetic -- the transport is in-process ASGI and never
    # touches a network -- but FastMCP's default host, "fastapi", is one the
    # app's host guard rightly refuses.
    key = os.getenv(API_KEY_ENV)
    client_kwargs: dict[str, Any] = {"base_url": "http://127.0.0.1"}
    if key:
        client_kwargs["headers"] = {"Authorization": f"Bearer {key}"}

    mcp = FastMCP.from_fastapi(
        app=app,
        httpx_client_kwargs=client_kwargs,
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
    return mcp


def _add_convenience_tools(mcp: FastMCP) -> None:
    """Tools that answer a question rather than mirror an endpoint."""

    @mcp.tool
    def garmin_status() -> dict[str, Any]:
        """Check whether this server can reach the Garmin account.

        Call this first if anything fails. It reports whether tokens exist and
        how old they are, without calling Garmin.
        """
        return {
            "version": __version__,
            "authenticated": auth.has_tokens(),
            "token_dir": str(auth.token_dir()),
            "token_age_days": round(auth.token_age_days() or 0, 1) if auth.has_tokens() else None,
            "hint": None if auth.has_tokens() else "Run `garmin login` in a terminal.",
        }

    @mcp.tool
    def find_exercise(query: str, limit: int = 10) -> list[dict[str, str]]:
        """Find Garmin's category and exercise_name for an exercise.

        Always use this before creating a workout or logging a session with an
        exercise whose Garmin name is not already known.
        """
        return [e._asdict() for e in catalogue.search(query, limit=limit)]

    @mcp.tool
    def recent_strength_sessions(count: int = 5, include_sets: bool = True) -> list[dict[str, Any]]:
        """Recent strength workouts, with their sets.

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

    @mcp.tool
    def week_summary(end_day: str | None = None) -> dict[str, Any]:
        """Seven days of training and health in one answer.

        Covers the week ending on end_day, or today. Gives each day's steps,
        resting heart rate, sleep and training readiness, plus the activities
        in that window.
        """
        # "Today" in the Garmin profile's timezone, not the machine's or UTC's:
        # after 8pm in New York, UTC is already tomorrow.
        last = date.fromisoformat(end_day) if end_day else auth.call("today")
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

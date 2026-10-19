"""Every tool's output must satisfy the tool's own output schema.

Strict MCP clients validate structured output against the advertised schema,
formats included, and reject the whole response on a single mismatch. A naive
datetime once slipped through here as a "date-time" with no offset, and every
list_activities call failed in Claude while the raw JSON-RPC probe looked fine.
These tests run the real tools over stubbed Garmin data and validate the result
the same way the client does.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from garmin_api.mcp import create_mcp

ACTIVITY = {
    "activityId": 18800000001,
    "activityName": "Pool Swim",
    "activityType": {"typeId": 27, "typeKey": "lap_swimming", "parentTypeId": 26},
    "startTimeGMT": "2026-08-30 22:15:00",
    "startTimeLocal": "2026-08-30 18:15:00",
    "duration": 2600.0,
    "distance": 2450.0,
    "calories": 260.0,
    "averageHR": 135.0,
}
STRENGTH = {
    **ACTIVITY,
    "activityId": 1,
    "activityName": "Push A",
    "activityType": {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
}
SETS = {
    "exerciseSets": [
        {
            "setType": "ACTIVE",
            "repetitionCount": 10,
            "weight": 60000.0,
            "duration": 30.0,
            "startTime": "2026-09-21T05:00:00.0",
            "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}],
        }
    ]
}
TYPES = [
    {"typeId": 17, "typeKey": "all", "parentTypeId": None},
    {"typeId": 29, "typeKey": "fitness_equipment", "parentTypeId": 17},
    {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
    {"typeId": 26, "typeKey": "swimming", "parentTypeId": 17},
    {"typeId": 27, "typeKey": "lap_swimming", "parentTypeId": 26},
]
STATS = {"totalSteps": 9312, "restingHeartRate": 58, "floorsAscended": 9.46}
SLEEP = {
    "dailySleepDTO": {
        "calendarDate": "2026-10-18",
        "sleepTimeSeconds": 25200,
        "sleepStartTimestampGMT": 1792300000000,
        "sleepEndTimestampGMT": 1792325000000,
        "sleepScores": {"overall": {"value": 74}},
    }
}

GARMIN = {
    "get_activities": [ACTIVITY, STRENGTH],
    "get_activities_by_date": [ACTIVITY, STRENGTH],
    # The single-activity endpoint nests its fields, unlike the list.
    "get_activity": {
        "activityId": 1,
        "activityName": "Push A",
        "activityTypeDTO": {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
        "summaryDTO": {
            "startTimeGMT": "2026-09-21T05:00:00.0",
            "startTimeLocal": "2026-09-21T07:00:00.0",
        },
    },
    "get_last_activity": ACTIVITY,
    "get_activity_exercise_sets": SETS,
    "get_activity_types": TYPES,
    "get_stats": STATS,
    "get_sleep_data": SLEEP,
    "get_hrv_data": {"hrvSummary": {"lastNightAvg": 61, "status": "LOW"}},
    "get_training_readiness": [{"score": 58}],
    "get_daily_weigh_ins": {"dateWeightList": [{"weight": 81400.0, "calendarDate": "2026-10-18"}]},
    "get_body_composition": {"dateWeightList": [{"weight": 81400.0, "calendarDate": "2026-10-18"}]},
    "get_stress_data": {
        "calendarDate": "2026-10-18",
        "avgStressLevel": 33,
        "maxStressLevel": 88,
        "stressValuesArray": [[1792296000000, -1], [1792296180000, 40]],
    },
    "get_steps_data": [{"startGMT": "2026-10-18T14:00:00.0", "steps": 8}],
    "get_personal_record": [
        {"id": 1, "typeId": 1, "value": 287.5, "prStartTimeGmtFormatted": "2025-09-14T13:20:00.0"}
    ],
    "get_devices": [{"deviceId": 3500000001, "displayName": "Example Watch", "deviceStatus": "active"}],
    "get_workouts": [
        {
            "workoutId": 7,
            "workoutName": "Push A",
            "sportType": {"sportTypeKey": "strength_training"},
            "updateDate": "2026-09-23T17:03:06.0",
        }
    ],
    "get_scheduled_workouts": {
        "calendarItems": [
            {
                "id": 1600000001,
                "itemType": "workout",
                "title": "Push A",
                "date": "2026-10-20",
                "sportTypeKey": "strength_training",
                "workoutId": 7,
            }
        ]
    },
}

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("list_activities", {"limit": 5}),
    ("list_activities", {"limit": 5, "activity_type": "strength_training"}),
    ("list_activities", {"start_date": "2026-08-01", "end_date": "2026-08-30"}),
    ("get_activity", {"activity_id": 1}),
    ("get_last_activity", {}),
    ("get_activity_sets", {"activity_id": 1}),
    ("get_day_summary", {"day": "2026-10-18"}),
    ("get_sleep", {"day": "2026-10-18"}),
    ("get_hrv", {"day": "2026-10-18"}),
    ("get_stress", {"day": "2026-10-18"}),
    ("get_steps", {"day": "2026-10-18"}),
    ("get_personal_records", {}),
    ("list_devices", {}),
    ("list_weigh_ins", {"start": "2026-10-01", "end": "2026-10-18"}),
    ("list_workouts", {}),
    ("list_scheduled_workouts", {"year": 2026, "month": 10}),
    ("recent_strength_sessions", {"count": 3}),
    ("week_summary", {"end_day": "2026-10-18"}),
    ("garmin_status", {}),
    ("find_exercise", {"query": "bench"}),
    ("search_exercises", {"q": "row"}),
]


@pytest.fixture(scope="module")
def mcp() -> Any:
    return create_mcp()


@pytest.fixture(scope="module")
def schemas(mcp: Any) -> dict[str, Any]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name: t.output_schema for t in tools}


@pytest.fixture(autouse=True)
def stub_garmin(fake_garmin: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from garmin_api.services import activities

    monkeypatch.setattr(activities, "_types_cache", None)
    fake_garmin["responses"].update(GARMIN)


@pytest.mark.parametrize(
    ("tool", "args"), CALLS, ids=[f"{t}-{i}" for i, (t, _) in enumerate(CALLS)]
)
def test_output_matches_the_advertised_schema(
    mcp: Any, schemas: dict[str, Any], tool: str, args: dict[str, Any]
) -> None:
    result = asyncio.run(mcp.call_tool(tool, args))
    content = result.structured_content
    schema = schemas[tool]
    assert content is not None, f"{tool} returned no structured content"
    if schema is None:
        return

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(content), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:5])


def test_the_format_checker_is_actually_checking() -> None:
    """Without rfc3339-validator installed, date-time checks pass silently."""
    checker = FormatChecker()
    assert not checker.conforms("2026-08-30T18:15:00", "date-time")
    assert checker.conforms("2026-08-30T18:15:00Z", "date-time")

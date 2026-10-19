"""Regressions from the second review round of 19 Oct 2026 (docs and Garmin field checks)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from garmin_api import catalogue
from garmin_api.http.app import create_app
from garmin_api.mcp import create_mcp
from garmin_api.mcp.server import INSTRUCTIONS
from garmin_api.models.activities import SetEdit, StrengthSet
from garmin_api.models.health import HrvSummary, SleepSummary
from garmin_api.models.workouts import (
    StrengthExerciseSpec,
    StrengthSessionExercise,
    StrengthSessionSpec,
    StrengthWorkoutSpec,
)
from garmin_api.services import activities, health, trends, uploads, workouts


def _hit(query: str) -> dict[str, str]:
    """A search result as find_exercise returns it, display name left out."""
    found = catalogue.search(query, 1)[0]._asdict()
    found.pop("name")
    return found


# --- Search results feed the write tools --------------------------------------


def test_a_search_result_keeps_its_variant_in_every_write_spec() -> None:
    hit = _hit("barbell bench press")
    assert StrengthExerciseSpec(**hit).exercise_name == "BARBELL_BENCH_PRESS"
    session = StrengthSessionExercise(**hit, sets=[{"reps": 5}])
    assert session.exercise_name == "BARBELL_BENCH_PRESS"


def test_editing_a_set_with_a_search_result_writes_the_variant(
    fake_garmin: dict[str, Any],
) -> None:
    sent: list[Any] = []
    rows = [{"setType": "ACTIVE", "repetitionCount": 8, "exercises": []}]
    fake_garmin["responses"].update(
        {
            "get_activity_exercise_sets": lambda *_: {"exerciseSets": rows},
            "set_activity_exercise_sets": lambda *a: sent.append(a[1]) or {},
        }
    )
    activities.edit_sets(1, SetEdit(changes=[{"sets": [1], **_hit("dumbbell row")}]))
    exercise = sent[0]["exerciseSets"][0]["exercises"][0]
    assert (exercise["category"], exercise["name"]) == ("ROW", "DUMBBELL_ROW")


def test_an_unknown_key_is_refused_rather_than_dropped() -> None:
    response = TestClient(create_app()).post(
        "/v1/workouts/strength",
        json={"name": "Push", "exercises": [{"category": "PUSH_UP", "weight": 20}]},
    )
    assert response.status_code == 422
    assert "weight" in response.text


# --- Logging sessions -----------------------------------------------------------

SESSION = {
    "name": "Push A",
    "notes": "Felt strong",
    "start_time": "2026-09-21T07:00:00+00:00",
    "exercises": [{"category": "PUSH_UP", "sets": [{"reps": 10}]}],
}


ACTIVITY_TYPES = [
    {"typeId": 17, "typeKey": "all", "parentTypeId": None},
    {"typeId": 29, "typeKey": "fitness_equipment", "parentTypeId": 17},
    {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
]


@pytest.fixture
def nothing_there_yet(fake_garmin: dict[str, Any], monkeypatch) -> dict[str, Any]:
    monkeypatch.setattr(activities, "_types_cache", None)
    fake_garmin["responses"].update({"get_activity_types": ACTIVITY_TYPES, "connectapi": []})
    return fake_garmin


def test_notes_become_the_activity_description(nothing_there_yet: dict[str, Any]) -> None:
    described: list[Any] = []
    nothing_there_yet["responses"].update(
        {
            "upload_activity": {"detailedImportResult": {"successes": [{"internalId": 5}]}},
            "set_activity_name": {},
            "set_activity_description": lambda *a: described.append(a) or {},
        }
    )
    result = uploads.log_strength_session(StrengthSessionSpec.model_validate(SESSION))
    assert described == [("5", "Felt strong")]
    assert result["description"] == "Felt strong"


def test_a_duplicate_leaves_the_existing_activity_alone(nothing_there_yet: dict[str, Any]) -> None:
    changed: list[Any] = []
    nothing_there_yet["responses"].update(
        {
            "upload_activity": {
                "detailedImportResult": {
                    "failures": [{"internalId": 111, "messages": [{"content": "Duplicate"}]}]
                }
            },
            "set_activity_name": lambda *a: changed.append(a) or {},
            "set_activity_description": lambda *a: changed.append(a) or {},
        }
    )
    result = uploads.log_strength_session(StrengthSessionSpec.model_validate(SESSION))
    assert result["status"] == "duplicate"
    assert changed == []


# --- Workouts -------------------------------------------------------------------


def test_creating_and_scheduling_returns_the_schedule(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"].update(
        {
            "upload_workout": {"workoutId": 42, "workoutName": "Push"},
            "schedule_workout": {"workoutScheduleId": 9001, "calendarDate": "2026-10-22"},
        }
    )
    spec = StrengthWorkoutSpec(
        name="Push", schedule_date=date(2026, 10, 22), exercises=[{"category": "PUSH_UP"}]
    )
    summary = workouts.create_strength(spec)
    assert (summary.schedule_id, summary.scheduled_on) == (9001, date(2026, 10, 22))


# --- Trends ---------------------------------------------------------------------


def test_change_is_the_movement_of_the_period_averages(fake_garmin: dict[str, Any]) -> None:
    """Two weeks: averages rise by 1000 while the last day is far below the first."""
    week_one = [10000] + [6666] * 6
    week_two = [8000] * 6 + [-1]
    days = [date(2026, 1, 5 + i) for i in range(14)]
    fake_garmin["responses"]["get_daily_steps"] = [
        {"calendarDate": d.isoformat(), "totalSteps": s}
        for d, s in zip(days, week_one + week_two[:6] + [1000], strict=True)
    ]
    trend = trends.trend("steps", "2026-01-05", "2026-01-18", aggregation="week")
    first, last = trend.summary["first_period_average"], trend.summary["last_period_average"]
    assert trend.summary["change"] == round(last - first, 1)


# --- Garmin's shapes ------------------------------------------------------------


def test_a_single_readiness_snapshot_is_read(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_training_readiness"] = {
        "calendarDate": "2026-01-01",
        "score": 72,
        "inputContext": "AFTER_WAKEUP_RESET",
    }
    assert health.day("2026-01-01", parts={"readiness"}).training_readiness == 72


@pytest.mark.parametrize("reply", [[], {}])
def test_an_empty_steps_reply_is_unknown_not_zero(fake_garmin: dict[str, Any], reply) -> None:
    fake_garmin["responses"]["get_steps_data"] = reply
    assert health.steps_summary("2026-01-01").total is None


def test_fractional_hrv_values_are_accepted() -> None:
    summary = HrvSummary.from_garmin(
        {"hrvSummary": {"calendarDate": "2026-01-01", "lastNightAvg": 48.5, "weeklyAvg": 52.5}}
    )
    assert (summary.last_night_avg, summary.weekly_avg) == (48.5, 52.5)


def test_the_days_weight_is_its_last_weigh_in(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_daily_weigh_ins"] = {
        "dateWeightList": [
            {"samplePk": 1, "weight": 72000, "timestampGMT": 1767254400000},
            {"samplePk": 2, "weight": 73500, "timestampGMT": 1767297600000},
        ]
    }
    assert health.day("2026-01-01", parts={"weight"}).weight.weight_kg == 73.5


def test_a_sets_exercise_is_its_likeliest_guess() -> None:
    row = {
        "setType": "ACTIVE",
        "exercises": [
            {"category": "SQUAT", "name": "BACK_SQUAT", "probability": 12.0},
            {"category": "DEADLIFT", "name": "BARBELL_DEADLIFT", "probability": 81.0},
        ],
    }
    assert StrengthSet.from_garmin(row).exercise_category == "DEADLIFT"


def test_sleep_times_never_come_from_the_local_fields() -> None:
    summary = SleepSummary.from_garmin(
        {
            "dailySleepDTO": {
                "calendarDate": "2026-10-18",
                "sleepStartTimestampLocal": 1792364400000,
                "sleepEndTimestampLocal": 1792393200000,
            }
        }
    )
    assert summary.start is None and summary.end is None


# --- What a model reads -----------------------------------------------------------


def test_the_instructions_warn_about_sharing_and_corrections() -> None:
    assert "Strava" in INSTRUCTIONS
    assert "edit_activity_sets" in INSTRUCTIONS


def test_the_log_tool_warns_about_sharing_and_relogging() -> None:
    tools = {t.name: t for t in asyncio.run(create_mcp().list_tools())}
    description = tools["log_strength_session"].description or ""
    assert "Strava" in description
    assert "edit_activity_sets" in description

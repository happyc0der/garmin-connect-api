"""The five 0.2 features, on made-up data in the shapes Garmin really returns.

Shapes were read from a real account on 19 Oct 2026; every value here is
invented, since this repository is public.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from garmin_api.errors import InvalidRequest
from garmin_api.models.records import describe, display
from garmin_api.models.workouts import EnduranceWorkoutSpec
from garmin_api.services import activities, health, trends, workouts

# --- Named records -----------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "label", "unit"),
    [
        ("pr.label.1k.run", "Fastest 1 km run", "s"),
        ("pr.label.half.marathon", "Fastest half marathon", "s"),
        ("pr.label.40k.cycle", "Fastest 40 km ride", "s"),
        ("pr.label.farthest.cycle", "Longest ride", "m"),
        ("pr.label.max.elev", "Most climbing in a ride", "m"),
        ("pr.label.100yd.poolswim", "Fastest 100 yd pool swim", "s"),
        ("pr.label.longest.poolswim", "Longest pool swim", "m"),
        ("pr.label.steps.best.week", "Most steps in a week", "steps"),
        ("pr.label.steps.longest.streak", "Longest step goal streak", "days"),
        ("pr.label.max.rep.weight.bench_press", "Heaviest bench press", "kg"),
    ],
)
def test_record_keys_become_labels_and_units(key: str, label: str, unit: str) -> None:
    assert describe(key) == (label, unit)


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (287.5, "s", "4:47.5"),
        (5021.0, "s", "1:23:41"),
        (3200.0, "m", "3.20 km"),
        (95.0, "m", "95 m"),
        (12345.0, "steps", "12,345 steps"),
        (40.0, "days", "40 days"),
    ],
)
def test_record_values_read_as_people_read_them(value: float, unit: str, text: str) -> None:
    assert display(value, unit) == text


def test_records_carry_names_and_come_newest_first(
    fake_garmin: dict[str, Any], monkeypatch
) -> None:
    monkeypatch.setattr(health, "_record_types", None)
    fake_garmin["responses"]["get_personal_record_types"] = [
        {"id": 1, "key": "pr.label.1k.run", "sport": "RUNNING"},
        {"id": 7, "key": "pr.label.farthest.run", "sport": "RUNNING"},
    ]
    fake_garmin["responses"]["get_personal_record"] = [
        {"id": 10, "typeId": 1, "value": 287.5, "prStartTimeGmtFormatted": "2025-05-01T07:00:00.0"},
        {
            "id": 11,
            "typeId": 7,
            "value": 3200.0,
            "prStartTimeGmtFormatted": "2025-06-01T07:00:00.0",
        },
    ]
    found = health.records()
    assert [r.label for r in found] == ["Longest run", "Fastest 1 km run"]
    assert found[1].display == "4:47.5"


# --- Laps and lengths --------------------------------------------------------

YARD = 22.86


def _length(i: int, seconds: float) -> dict[str, Any]:
    return {
        "lengthIndex": i,
        "distance": YARD,
        "duration": seconds,
        "swimStroke": "FREESTYLE",
        "totalNumberOfStrokes": 10,
        "averageSWOLF": 30.0,
    }


def test_a_yard_pool_swim_is_paced_per_100_yards(fake_garmin: dict[str, Any]) -> None:
    lengths = [_length(i, 25.0) for i in range(1, 5)] + [_length(i, 30.0) for i in range(5, 9)]
    fake_garmin["responses"].update(
        {
            "get_activity": {
                "activityTypeDTO": {"typeKey": "lap_swimming"},
                "summaryDTO": {"poolLength": 25.0, "unitOfPoolLength": {"unitKey": "yard"}},
            },
            "get_activity_splits": {
                "lapDTOs": [
                    {"lapIndex": 1, "distance": YARD * 8, "duration": 220.0, "lengthDTOs": lengths}
                ]
            },
        }
    )
    laps = activities.laps(1)
    assert (laps.pool_length, laps.pool_unit) == (25.0, "yd")
    assert [s.pace for s in laps.segments or []] == ["1:40/100yd", "2:00/100yd"]
    assert laps.summary["distance"] == 200.0
    assert laps.summary["first_half_pace"] == "1:40/100yd"
    assert laps.summary["fastest_segment"]["lengths"] == "1-4"


def test_a_ride_is_summarised_on_moving_time(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"].update(
        {
            "get_activity": {"activityTypeDTO": {"typeKey": "road_biking"}, "summaryDTO": {}},
            "get_activity_splits": {
                "lapDTOs": [
                    {
                        "lapIndex": 1,
                        "distance": 10000.0,
                        "duration": 1500.0,
                        "movingDuration": 1200.0,
                        "averageSpeed": 8.33,
                    },
                    {
                        "lapIndex": 2,
                        "distance": 10000.0,
                        "duration": 1200.0,
                        "movingDuration": 1200.0,
                        "averageSpeed": 8.33,
                    },
                ]
            },
        }
    )
    summary = activities.laps(1).summary
    assert summary["average_speed_kph"] == 30.0, "moving time, not elapsed"
    assert summary["elapsed_time_s"] == 2700.0


def test_a_run_reports_pace_per_km_and_no_location(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"].update(
        {
            "get_activity": {"activityTypeDTO": {"typeKey": "running"}, "summaryDTO": {}},
            "get_activity_splits": {
                "lapDTOs": [
                    {
                        "lapIndex": 1,
                        "distance": 1000.0,
                        "duration": 300.0,
                        "endLatitude": 1.0,
                        "endLongitude": 2.0,
                    }
                ]
            },
        }
    )
    laps = activities.laps(1)
    assert laps.laps[0].pace == "5:00/km"
    assert "Latitude" not in laps.model_dump_json()


# --- Trends ------------------------------------------------------------------


def test_steps_trend_has_points_and_a_summary(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_daily_steps"] = [
        {"calendarDate": "2026-01-01", "totalSteps": 4000, "stepGoal": 5000},
        {"calendarDate": "2026-01-02", "totalSteps": 6000, "stepGoal": 5000},
    ]
    t = trends.trend("steps", "2026-01-01", "2026-01-02")
    assert t.aggregation.value == "day"
    assert [p.value for p in t.points] == [4000.0, 6000.0]
    assert (t.summary["average"], t.summary["change"]) == (5000.0, 2000.0)


def test_readiness_takes_the_morning_score(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_training_readiness_range"] = [
        {"calendarDate": "2026-01-01", "score": 40, "inputContext": "UPDATE_REALTIME_VARIABLES"},
        {"calendarDate": "2026-01-01", "score": 70, "inputContext": "AFTER_WAKEUP_RESET"},
        {"calendarDate": "2026-01-01", "score": 55, "inputContext": "UPDATE_REALTIME_VARIABLES"},
    ]
    assert [p.value for p in trends.trend("readiness", "2026-01-01", "2026-01-01").points] == [70.0]


def test_long_ranges_roll_up_into_weeks(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_rhr_daily"] = [
        {"calendarDate": f"2026-01-{d:02d}", "value": 50 + d % 2} for d in range(5, 19)
    ]
    t = trends.trend("resting_hr", "2026-01-01", "2026-04-30")
    assert t.aggregation.value == "week"
    assert [p.period for p in t.points] == [date(2026, 1, 5), date(2026, 1, 12)]
    assert all(p.days == 7 for p in t.points)


def test_running_and_cycling_vo2max_stay_separate(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_max_metrics_range"] = lambda *a, **k: [
        {
            "generic": {"calendarDate": "2026-01-10", "vo2MaxPreciseValue": 50.1},
            "cycling": {"calendarDate": "2026-01-11", "vo2MaxPreciseValue": 45.2},
        }
    ]
    assert [p.value for p in trends.trend("vo2max", "2026-01-01", "2026-03-01").points] == [50.1]
    assert [p.value for p in trends.trend("vo2max_cycling", "2026-01-01", "2026-03-01").points] == [
        45.2
    ]


def test_a_range_over_two_years_is_refused() -> None:
    with pytest.raises(InvalidRequest, match="limited"):
        trends.trend("steps", "2020-01-01", "2026-01-01")


# --- Swim, run and ride workouts -----------------------------------------------


def _flat(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in steps:
        out.append(s)
        out.extend(_flat(s.get("workoutSteps") or []))
    return out


def test_a_swim_carries_pool_strokes_kit_and_wall_rests() -> None:
    dto = workouts.build_endurance(
        EnduranceWorkoutSpec(
            sport="swimming",
            name="Swim",
            pool_length=25,
            pool_unit="yd",
            steps=[
                {
                    "repeat": 4,
                    "steps": [
                        {
                            "distance": 100,
                            "distance_unit": "yd",
                            "stroke": "free",
                            "equipment": "paddles",
                        },
                        {"kind": "rest", "duration_s": 15},
                    ],
                }
            ],
        )
    )
    assert (dto["poolLength"], dto["poolLengthUnit"]["unitKey"]) == (25.0, "yard")
    steps = _flat(dto["workoutSegments"][0]["workoutSteps"])
    swim, rest = steps[1], steps[2]
    assert swim["endConditionValue"] == pytest.approx(91.44)
    assert (swim["strokeType"]["strokeTypeKey"], swim["equipmentType"]["equipmentTypeKey"]) == (
        "free",
        "paddles",
    )
    assert rest["endCondition"]["conditionTypeKey"] == "fixed.rest"
    orders = [s["stepOrder"] for s in steps]
    assert len(orders) == len(set(orders))


def test_a_run_pace_band_becomes_speeds_slower_first() -> None:
    dto = workouts.build_endurance(
        EnduranceWorkoutSpec(
            sport="running",
            name="Run",
            steps=[
                {"distance": 1, "distance_unit": "km", "target": {"pace_per_km": ["5:00", "4:00"]}}
            ],
        )
    )
    step = dto["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert (step["targetValueOne"], step["targetValueTwo"]) == (
        pytest.approx(1000 / 300),
        4.1666666666666667,
    )


@pytest.mark.parametrize(
    ("spec", "fault"),
    [
        (
            dict(sport="swimming", name="x", pool_length=25, steps=[{"distance": 110}]),
            "whole number",
        ),
        (
            dict(sport="running", name="x", steps=[{"duration_s": 60, "stroke": "free"}]),
            "for swims",
        ),
        (
            dict(sport="running", name="x", steps=[{"duration_s": 60, "distance": 1}]),
            "exactly one of",
        ),
        (
            dict(
                sport="running",
                name="x",
                steps=[{"duration_s": 60, "target": {"pace_per_km": ["x", "4:40"]}}],
            ),
            "5:30",
        ),
        (
            dict(
                sport="swimming",
                name="x",
                steps=[{"duration_s": 60, "target": {"heart_rate_zone": 2}}],
            ),
            "runs and rides",
        ),
    ],
)
def test_a_bad_workout_is_refused_with_its_reason(spec: dict[str, Any], fault: str) -> None:
    with pytest.raises(ValidationError, match=fault):
        EnduranceWorkoutSpec(**spec)


# --- Editing sets ---------------------------------------------------------------


def _sets_payload() -> dict[str, Any]:
    def active(i: int, name: str) -> dict[str, Any]:
        return {
            "messageIndex": i,
            "setType": "ACTIVE",
            "repetitionCount": 10,
            "weight": 20000.0,
            "exercises": [{"category": "CURL", "name": name, "probability": 60.0}],
        }

    return {
        "activityId": 9,
        "exerciseSets": [
            active(0, "DUMBBELL_BICEPS_CURL"),
            {"messageIndex": 1, "setType": "REST", "duration": 60.0},
            active(2, "DUMBBELL_BICEPS_CURL"),
            {"messageIndex": 3, "setType": "REST", "duration": 60.0},
            active(4, "DUMBBELL_BICEPS_CURL"),
        ],
    }


@pytest.fixture
def sets_api(fake_garmin: dict[str, Any]) -> list[dict[str, Any]]:
    written: list[dict[str, Any]] = []
    state = {"payload": _sets_payload()}

    def write(_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
        written.append(payload)
        state["payload"] = payload
        return {}

    fake_garmin["responses"]["get_activity_exercise_sets"] = lambda *_: state["payload"]
    fake_garmin["responses"]["set_activity_exercise_sets"] = write
    return written


def test_sets_can_be_relabelled_and_reweighed(sets_api: list[dict[str, Any]]) -> None:
    from garmin_api.models.activities import SetEdit

    result = activities.edit_sets(
        9,
        SetEdit(
            changes=[
                {
                    "sets": [2, 3],
                    "category": "ROW",
                    "exercise_name": "DUMBBELL_ROW",
                    "weight_kg": 22.5,
                }
            ]
        ),
    )
    sent = sets_api[0]["exerciseSets"]
    assert sent[0]["exercises"][0]["name"] == "DUMBBELL_BICEPS_CURL", "set 1 untouched"
    assert sent[2]["exercises"] == [
        {"category": "ROW", "name": "DUMBBELL_ROW", "probability": 100.0}
    ]
    assert sent[4]["weight"] == 22500.0, "grams, as Garmin stores them"
    assert [s.exercise_name for s in result.sets] == [
        "DUMBBELL_BICEPS_CURL",
        "DUMBBELL_ROW",
        "DUMBBELL_ROW",
    ]


def test_a_category_alone_is_sent_with_no_name(sets_api: list[dict[str, Any]]) -> None:
    from garmin_api.models.activities import SetEdit

    activities.edit_sets(9, SetEdit(changes=[{"sets": [1], "category": "LUNGE"}]))
    assert sets_api[0]["exerciseSets"][0]["exercises"][0] == {
        "category": "LUNGE",
        "name": None,
        "probability": 100.0,
    }


def test_a_set_can_be_deleted(sets_api: list[dict[str, Any]]) -> None:
    from garmin_api.models.activities import SetEdit

    result = activities.edit_sets(9, SetEdit(delete=[2]))
    assert len([r for r in sets_api[0]["exerciseSets"] if r["setType"] == "ACTIVE"]) == 2
    assert [s.set_number for s in result.sets] == [1, 2]


@pytest.mark.parametrize(
    ("edit", "fault"),
    [
        ({"changes": [{"sets": [4], "reps": 5}]}, "No working set"),
        ({"changes": [{"sets": [1], "reps": 5}], "delete": [1]}, "both changed and deleted"),
        (
            {"changes": [{"sets": [1], "category": "ROW", "exercise_name": "NOT_A_ROW"}]},
            "Unknown exercise",
        ),
        ({}, "Nothing to change"),
    ],
)
def test_bad_edits_are_refused_before_writing(
    sets_api: list[dict[str, Any]], edit: dict[str, Any], fault: str
) -> None:
    from garmin_api.models.activities import SetEdit

    with pytest.raises(InvalidRequest, match=fault):
        activities.edit_sets(9, SetEdit(**edit))
    assert sets_api == [], "nothing written"


def test_a_change_must_change_something() -> None:
    from garmin_api.models.activities import SetChange

    with pytest.raises(ValidationError, match="give an exercise"):
        SetChange(sets=[1])
    with pytest.raises(ValidationError, match="category"):
        SetChange(sets=[1], exercise_name="DUMBBELL_ROW")

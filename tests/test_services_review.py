"""Regressions from the services and models review of 19 Oct 2026."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from garmin_api.errors import InvalidRequest, RateLimited
from garmin_api.models.health import SleepSummary
from garmin_api.models.workouts import (
    EnduranceWorkoutSpec,
    StrengthExerciseSpec,
    StrengthWorkoutSpec,
    Target,
)
from garmin_api.services import activities, health, trends, weight, workouts

READINESS = [
    {"calendarDate": "2026-01-01", "score": 40, "inputContext": "UPDATE_REALTIME_VARIABLES"},
    {"calendarDate": "2026-01-01", "score": 70, "inputContext": "AFTER_WAKEUP_RESET"},
]


def test_sleep_spo2_and_respiration_are_read_from_the_nightly_summary() -> None:
    summary = SleepSummary.from_garmin(
        {
            "dailySleepDTO": {
                "calendarDate": "2026-01-01",
                "averageSpO2Value": 95.0,
                "averageRespirationValue": 14.0,
            }
        }
    )
    assert (summary.average_spo2, summary.average_respiration) == (95.0, 14.0)


def test_an_end_date_alone_is_refused_rather_than_ignored(fake_garmin: dict[str, Any]) -> None:
    with pytest.raises(InvalidRequest, match="start_date"):
        activities.list_activities(end_date="2025-12-31")
    assert fake_garmin["calls"] == []


def test_a_failed_record_type_request_is_not_cached(
    fake_garmin: dict[str, Any], monkeypatch
) -> None:
    monkeypatch.setattr(health, "_record_types", None)
    answers: list[Any] = [RuntimeError("503"), [{"id": 1, "key": "pr.label.1k.run"}]]

    def types(*_: Any) -> Any:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    fake_garmin["responses"]["get_personal_record_types"] = types
    assert health.record_types() == {}
    assert health.record_types()[1]["label"] == "Fastest 1 km run"


def test_records_with_no_date_come_last(fake_garmin: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(health, "_record_types", None)
    fake_garmin["responses"].update(
        {
            "get_personal_record_types": [],
            "get_personal_record": [
                {"id": 12, "typeId": 1, "value": 1.0},
                {
                    "id": 10,
                    "typeId": 1,
                    "value": 1.0,
                    "prStartTimeGmtFormatted": "2026-10-01T07:00:00.0",
                },
                {
                    "id": 11,
                    "typeId": 1,
                    "value": 1.0,
                    "prStartTimeGmtFormatted": "2025-05-01T07:00:00.0",
                },
            ],
        }
    )
    assert [r.record_id for r in health.records()] == [10, 11, 12]


def test_an_outage_on_steps_is_an_error_not_zero(fake_garmin: dict[str, Any]) -> None:
    """Asked for one metric alone, an outage must not look like a day of no steps."""
    from garmin_api.errors import GarminUnavailable

    def outage(*_: Any) -> Any:
        raise GarminUnavailable("Garmin answered with a server error (503).")

    fake_garmin["responses"]["get_steps_data"] = outage
    with pytest.raises(GarminUnavailable):
        health.steps_summary("2026-01-01")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/v1/health/sleep/2026-10-18", "get_sleep_data"),
        ("/v1/health/hrv/2026-10-18", "get_hrv_data"),
        ("/v1/health/stress/2026-10-18", "get_stress_data"),
        ("/v1/health/training-readiness/2026-10-18", "get_training_readiness"),
        ("/v1/health/records", "get_personal_record"),
    ],
)
def test_an_outage_on_one_metric_is_a_502_not_no_data(
    fake_garmin: dict[str, Any], path: str, method: str
) -> None:
    from fastapi.testclient import TestClient

    from garmin_api.errors import GarminUnavailable
    from garmin_api.http.app import create_app

    def outage(*_: Any) -> Any:
        raise GarminUnavailable("Garmin answered with a server error (503).")

    fake_garmin["responses"].update({method: outage, "get_personal_record_types": []})
    assert TestClient(create_app()).get(path).status_code == 502


def test_an_outage_on_one_part_leaves_the_rest_of_the_day(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.errors import GarminUnavailable

    def outage(*_: Any) -> Any:
        raise GarminUnavailable("Garmin answered with a server error (503).")

    fake_garmin["responses"].update({"get_stats": {"totalSteps": 9000}, "get_sleep_data": outage})
    summary = health.day("2026-01-01", parts={"stats", "sleep"})
    assert (summary.steps, summary.sleep) == (9000, None)


def test_a_rate_limit_on_steps_still_stops(fake_garmin: dict[str, Any]) -> None:
    def limited(*_: Any) -> Any:
        raise RateLimited("slow down")

    fake_garmin["responses"]["get_steps_data"] = limited
    with pytest.raises(RateLimited):
        health.steps_summary("2026-01-01")


def test_the_day_and_the_trend_agree_on_readiness(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"].update(
        {"get_training_readiness": READINESS, "get_training_readiness_range": READINESS}
    )
    day = health.day("2026-01-01", parts={"readiness"})
    trend = trends.trend("readiness", "2026-01-01", "2026-01-01")
    assert day.training_readiness == 70
    assert [p.value for p in trend.points] == [70.0]


def test_a_weigh_in_given_in_utc_lands_on_the_local_day(fake_garmin: dict[str, Any]) -> None:
    sent: list[Any] = []
    fake_garmin["responses"].update(
        {
            "_user_tzinfo": ZoneInfo("America/New_York"),
            "add_weigh_in": lambda *a: sent.append(a) or {},
        }
    )
    weight.add_weigh_in(72.4, timestamp=datetime(2026, 10, 19, 2, 0, tzinfo=UTC))
    assert sent == [(72.4, "kg", "2026-10-18T22:00:00-04:00")]


def test_a_weigh_in_with_no_offset_is_passed_on_as_local(fake_garmin: dict[str, Any]) -> None:
    sent: list[Any] = []
    fake_garmin["responses"]["add_weigh_in"] = lambda *a: sent.append(a) or {}
    weight.add_weigh_in(72.4, timestamp="2026-10-18T22:00:00")
    assert sent == [(72.4, "kg", "2026-10-18T22:00:00")]


def test_a_failed_schedule_keeps_the_new_workouts_id(fake_garmin: dict[str, Any]) -> None:
    def refuse(*_: Any) -> Any:
        raise InvalidRequest("Garmin rejected the request (400).")

    fake_garmin["responses"].update(
        {"upload_workout": {"workoutId": 4242, "workoutName": "Push"}, "schedule_workout": refuse}
    )
    spec = StrengthWorkoutSpec(
        name="Push",
        schedule_date=date(2026, 10, 22),
        exercises=[StrengthExerciseSpec(category="PUSH_UP")],
    )
    with pytest.raises(InvalidRequest) as caught:
        workouts.create_strength(spec)
    assert "4242" in (caught.value.detail or "")


def test_a_workout_exercise_given_by_category_alone_is_the_generic_one() -> None:
    spec = StrengthWorkoutSpec(name="Push", exercises=[StrengthExerciseSpec(category="push_up")])
    assert spec.exercises[0].exercise_name == "PUSH_UP"
    assert workouts.build_strength(spec)["workoutSegments"]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ({"pace_per_km": ("5:00", "5:30")}, ("pace_per_km", ("5:30", "5:00"))),
        ({"heart_rate_bpm": (150, 140)}, ("heart_rate_bpm", (140, 150))),
        ({"speed_kph": (30.0, 25.0)}, ("speed_kph", (25.0, 30.0))),
    ],
)
def test_a_range_given_either_way_round_is_put_in_order(given, expected) -> None:
    name, value = expected
    assert getattr(Target(**given), name) == value


@pytest.mark.parametrize(
    "given",
    [
        {"pace_per_km": ("0:00", "0:00")},
        {"pace_per_km": ("5:75", "5:00")},
        {"heart_rate_bpm": (-5, 150)},
        {"power_watts": (0, 0)},
    ],
)
def test_an_impossible_target_is_refused_by_the_spec(given) -> None:
    with pytest.raises(ValidationError):
        Target(**given)


def test_a_reversed_pace_builds_a_workout() -> None:
    spec = EnduranceWorkoutSpec.model_validate(
        {
            "name": "Tempo",
            "sport": "running",
            "steps": [
                {"distance": 5, "distance_unit": "km", "target": {"pace_per_km": ["5:00", "5:30"]}}
            ],
        }
    )
    assert workouts.build_endurance(spec)["workoutSegments"]


def _monthly_vo2(*_: Any, **__: Any) -> list[dict[str, Any]]:
    return [
        {"generic": {"calendarDate": f"2025-{m:02d}-01", "vo2MaxPreciseValue": 50.0 + m / 10}}
        for m in range(1, 13)
    ]


def test_vo2max_points_garmin_grouped_are_counted_as_periods(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_max_metrics_range"] = _monthly_vo2
    trend = trends.trend("vo2max", "2025-01-01", "2025-12-31")
    assert {p.days for p in trend.points} == {None}
    assert trend.summary["periods_with_data"] == 12
    assert "days_with_data" not in trend.summary


def test_a_vo2max_month_overlapping_the_start_is_kept(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_max_metrics_range"] = _monthly_vo2
    trend = trends.trend("vo2max", "2025-01-15", "2025-12-31", aggregation="month")
    assert trend.points[0].period == date(2025, 1, 1)
    assert len(trend.points) == 12


def test_daily_metrics_still_count_days(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_training_readiness_range"] = READINESS
    assert trends.trend("readiness", "2026-01-01", "2026-01-01").summary["days_with_data"] == 1


GARMIN_LIMITS = {
    "get_daily_stress_stats": 28,
    "get_daily_body_battery_stats": 28,
    "get_hrv_data_range": 366,
    "get_training_readiness_range": 28,
}


def _strict(name: str, calls: list[tuple[date, date]]) -> Any:
    """An endpoint that refuses ranges over its limit, as Garmin does."""

    def answer(start: str, end: str) -> Any:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        if (e - s).days + 1 > GARMIN_LIMITS[name]:
            raise InvalidRequest("Garmin rejected the request (400).")
        calls.append((s, e))
        return {"hrvSummaries": []} if name == "get_hrv_data_range" else []

    return answer


@pytest.mark.parametrize(
    ("metric", "endpoint"),
    [
        ("stress", "get_daily_stress_stats"),
        ("body_battery", "get_daily_body_battery_stats"),
        ("hrv", "get_hrv_data_range"),
        ("readiness", "get_training_readiness_range"),
    ],
)
@pytest.mark.parametrize("days", [1, 28, 29, 62, 366, 731])
def test_long_ranges_are_fetched_within_garmins_limits(
    fake_garmin: dict[str, Any], metric: str, endpoint: str, days: int
) -> None:
    calls: list[tuple[date, date]] = []
    fake_garmin["responses"][endpoint] = _strict(endpoint, calls)
    start = date(2024, 10, 20)
    end = start + timedelta(days=days - 1)
    trends.trend(metric, start, end)
    covered = [s + timedelta(days=i) for s, e in calls for i in range((e - s).days + 1)]
    assert covered == [start + timedelta(days=i) for i in range(days)]


def test_a_target_range_may_start_at_zero() -> None:
    """0.2.0 accepted "up to 90 rpm" as (0, 90); 0.2.1 briefly refused it."""
    assert Target(cadence=(0, 90)).cadence == (0, 90)

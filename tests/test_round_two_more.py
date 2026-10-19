"""Regressions from the second review round: regressions, fuzzing and property tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.set_message import SetMessage
from fit_tool.profile.profile_type import FileType, SetType
from pydantic import ValidationError

from garmin_api import catalogue
from garmin_api.errors import GarminUnavailable, InvalidRequest
from garmin_api.fit import decode_strength_session, encode_strength_session
from garmin_api.fit.body import measurement_serial
from garmin_api.http.app import create_app
from garmin_api.models.health import BodyCompositionSpec, StressSummary
from garmin_api.models.workouts import (
    EnduranceStep,
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
)
from garmin_api.services import activities, uploads, workouts

# --- The catalogue ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Banded Shoulder Adduction",  # the catalogue has Abduction, the opposite movement
        "Overhand Carry",  # and Overhead Carry
        "machine row",  # not the Rowing Machine
        "cable fly",  # not a single-arm reverse fly
        "chest fly",  # not the suspension version
        "dumbbell rdl",  # not the single-leg version
    ],
)
def test_match_does_not_swap_one_lift_for_another(name: str) -> None:
    assert catalogue.match(name) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pullups", "Pull-up"),
        ("pushups", "Push-up"),
        ("chinups", "Chin-up"),
        ("situps", "Sit-up"),
        ("lat pulldowns", "Lat Pull-down"),
        ("dumbbell flyes", "Dumbbell Fly"),
        ("kettlebell flies", "Kettlebell Fly"),
        ("triceps pushdown", "Triceps Press-down"),
        ("farmer carry", "Farmer's Carry"),
        ("Hack Squat", "Barbell Hack Squat"),
        ("benhc press", "Bench Press"),
    ],
)
def test_match_finds_names_spelled_another_way(name: str, expected: str) -> None:
    found = catalogue.match(name)
    assert found is not None and found.name == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("dumbbell flies", "Dumbbell Fly"),
        ("farmer's carries", "Farmer's Carry"),
        ("Single-leg RDL Circuit", "Single-leg RDL Circuit"),
        ("toes to bar", "Toes to Bar"),
    ],
)
def test_search_puts_the_named_exercise_first(query: str, expected: str) -> None:
    assert catalogue.search(query, 1)[0].name == expected


def test_toes_does_not_stem_to_to() -> None:
    assert all("Toes" in e.name for e in catalogue.search("toes to bar", 10))


def test_validating_a_category_alone_accepts_its_generic_entry() -> None:
    assert catalogue.validate("BENCH_PRESS").name == "Bench Press"


# --- The FIT codec -------------------------------------------------------------------


def _session(*sets: StrengthSessionSet, start: datetime) -> StrengthSessionSpec:
    return StrengthSessionSpec(
        start_time=start,
        exercises=[
            StrengthSessionExercise(
                category="BENCH_PRESS", exercise_name="BARBELL_BENCH_PRESS", sets=list(sets)
            )
        ],
    )


def test_decoding_keeps_the_sessions_timezone() -> None:
    start = datetime(2026, 9, 21, 7, 0, tzinfo=timezone(timedelta(hours=-4)))
    spec = _session(StrengthSessionSet(reps=5, duration_s=30), start=start)
    decoded = decode_strength_session(encode_strength_session(spec))
    assert decoded.start_time.utcoffset() == timedelta(hours=-4)
    assert encode_strength_session(decoded) == encode_strength_session(spec)


def test_local_timestamp_differs_from_utc_by_exactly_the_offset() -> None:
    start = datetime(2026, 9, 21, 7, 0, tzinfo=timezone(timedelta(hours=2)))
    spec = _session(StrengthSessionSet(reps=5, duration_s=30.6), start=start)
    records = [r.message for r in FitFile.from_bytes(encode_strength_session(spec)).records]
    activity = next(m for m in records if isinstance(m, ActivityMessage))
    utc_fit_seconds = round(activity.timestamp / 1000) - 631065600
    assert activity.local_timestamp - utc_fit_seconds == 7200


def test_a_rest_shorter_than_a_millisecond_is_no_rest() -> None:
    start = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    spec = _session(
        StrengthSessionSet(reps=5, duration_s=30, rest_s=0.0004),
        StrengthSessionSet(reps=5, duration_s=30),
        start=start,
    )
    decoded = decode_strength_session(encode_strength_session(spec))
    assert [s.rest_s for s in decoded.exercises[0].sets] == [0.0, None]
    assert encode_strength_session(decoded) == encode_strength_session(spec)


def test_the_weigh_in_serial_follows_the_file() -> None:
    utc = BodyCompositionSpec(timestamp=datetime(2026, 1, 1, 5, 0, tzinfo=UTC), weight_kg=80)
    elsewhere = BodyCompositionSpec(
        timestamp=datetime(2026, 1, 1, 7, 0, tzinfo=timezone(timedelta(hours=2))), weight_kg=80
    )
    assert measurement_serial(utc) == measurement_serial(elsewhere)


@pytest.mark.parametrize("year", [1985, 2200])
def test_a_time_fit_cannot_hold_is_refused_by_the_spec(year: int) -> None:
    with pytest.raises(ValidationError):
        _session(StrengthSessionSet(reps=5), start=datetime(year, 1, 1, tzinfo=UTC))
    with pytest.raises(ValidationError):
        BodyCompositionSpec(timestamp=datetime(year, 1, 1, tzinfo=UTC), weight_kg=80)


def test_a_session_too_long_for_fit_is_a_clear_error() -> None:
    sets = [StrengthSessionSet(reps=1, duration_s=86400, rest_s=86400) for _ in range(30)]
    with pytest.raises(InvalidRequest, match="cannot be written"):
        encode_strength_session(_session(*sets, start=datetime(2026, 1, 1, tzinfo=UTC)))


def test_an_infinite_distance_is_refused() -> None:
    with pytest.raises(ValidationError):
        EnduranceStep.model_validate({"distance": float("inf")})
    with pytest.raises(ValidationError):
        EnduranceStep.model_validate({"distance": 1e308, "distance_unit": "mi"})


def _file_with_an_unidentified_set() -> bytes:
    """Bench, rest 60, a set the watch could not name, rest 90, bench."""
    t0 = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    builder = FitFileBuilder(auto_define=True)
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.serial_number = 1
    file_id.time_created = round(t0.timestamp() * 1000)
    builder.add(file_id)
    cursor = t0
    for kind, seconds, category, reps in [
        (SetType.ACTIVE, 30, 0, 5),
        (SetType.REST, 60, None, None),
        (SetType.ACTIVE, 30, 0xFFFF, 8),
        (SetType.REST, 90, None, None),
        (SetType.ACTIVE, 30, 0, 5),
    ]:
        message = SetMessage()
        message.set_type = kind
        message.start_time = round(cursor.timestamp() * 1000)
        message.duration = float(seconds)
        if category is not None:
            message.category = [category]
            message.repetitions = reps
        builder.add(message)
        cursor += timedelta(seconds=seconds)
    return bytes(builder.build().to_bytes())


def test_an_unidentified_set_is_kept_and_keeps_its_own_rest() -> None:
    decoded = decode_strength_session(_file_with_an_unidentified_set())
    flat = [(e.category, s.reps, s.rest_s) for e in decoded.exercises for s in e.sets]
    assert flat == [("BENCH_PRESS", 5, 60.0), ("UNKNOWN", 8, 90.0), ("BENCH_PRESS", 5, None)]


def test_back_to_back_sets_take_the_gap_between_them_as_rest() -> None:
    t0 = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    builder = FitFileBuilder(auto_define=True)
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.serial_number = 1
    file_id.time_created = round(t0.timestamp() * 1000)
    builder.add(file_id)
    for offset in (0, 75):  # 30 s of work, then 45 s before the next set
        message = SetMessage()
        message.set_type = SetType.ACTIVE
        message.start_time = round((t0 + timedelta(seconds=offset)).timestamp() * 1000)
        message.duration = 30.0
        message.category = [0]
        message.repetitions = 5
        builder.add(message)
    decoded = decode_strength_session(bytes(builder.build().to_bytes()))
    assert decoded.exercises[0].sets[0].rest_s == 45.0


def test_a_file_with_values_out_of_range_is_the_files_fault() -> None:
    """A set of 2,000 reps is beyond what a session may say; the file is at fault."""
    t0 = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    builder = FitFileBuilder(auto_define=True)
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.serial_number = 1
    file_id.time_created = round(t0.timestamp() * 1000)
    builder.add(file_id)
    message = SetMessage()
    message.set_type = SetType.ACTIVE
    message.start_time = round(t0.timestamp() * 1000)
    message.duration = 30.0
    message.category = [0]
    message.repetitions = 2000
    builder.add(message)
    data = bytes(builder.build().to_bytes())
    with pytest.raises(InvalidRequest, match="out of range"):
        decode_strength_session(data)
    client = TestClient(create_app())
    response = client.post("/v1/sessions/strength/decode", files={"file": ("s.fit", data)})
    assert response.status_code == 400
    named = client.post(
        "/v1/sessions/strength/decode", params={"name": "x" * 81}, files={"file": ("s.fit", data)}
    )
    assert named.status_code == 422


# --- The guard and the MCP over HTTP ----------------------------------------------------


def test_an_encoded_slash_cannot_steer_a_call_to_another_route(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["unschedule_workout"] = {}
    response = TestClient(create_app()).delete("/v1/workouts/schedule%2F1600000001")
    assert response.status_code == 400
    assert fake_garmin["calls"] == []


@pytest.mark.parametrize("headers", [{"Origin": "http://["}, {"Host": "[::1"}])
def test_a_malformed_header_is_a_json_400(headers: dict[str, str]) -> None:
    response = TestClient(create_app()).get("/healthz", headers=headers)
    assert response.status_code == 400
    assert response.json()["error"] == "Malformed Host or Origin"


def test_a_default_port_matches_a_missing_one() -> None:
    response = TestClient(create_app()).post(
        "/v1/health/weight",
        params={"weight": -1},
        headers={"Origin": "http://localhost", "Host": "localhost:80"},
    )
    assert response.status_code != 403


def test_mcp_over_http_writes_with_a_same_origin_header(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.mcp import create_mcp

    fake_garmin["responses"]["delete_workout"] = {}
    headers = {
        "accept": "application/json, text/event-stream",
        "origin": "http://127.0.0.1:8000",
    }
    hello = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "delete_workout", "arguments": {"workout_id": 123}},
    }
    ready = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    with TestClient(create_mcp().http_app(), base_url="http://127.0.0.1:8000") as client:
        started = client.post("/mcp", json=hello, headers=headers)
        session = {**headers, "mcp-session-id": started.headers.get("mcp-session-id", "")}
        client.post("/mcp", json=ready, headers=session)
        reply = client.post("/mcp", json=call, headers=session)
    assert reply.status_code == 200, reply.text
    assert "Cross-origin" not in reply.text
    assert ("delete_workout", (123,)) in fake_garmin["calls"]


# --- Uploads ---------------------------------------------------------------------------


def test_a_failed_search_before_the_upload_does_not_stop_it(
    fake_garmin: dict[str, Any], monkeypatch
) -> None:
    monkeypatch.setattr(activities, "_types_cache", None)

    def down(*_: Any, **__: Any) -> Any:
        raise GarminUnavailable("Garmin answered with a server error (500).")

    fake_garmin["responses"].update(
        {
            "get_activity_types": down,
            "upload_activity": {"detailedImportResult": {"successes": [{"internalId": 999}]}},
            "set_activity_name": {},
        }
    )
    spec = _session(StrengthSessionSet(reps=5), start=datetime(2026, 9, 21, 7, tzinfo=UTC))
    assert uploads.log_strength_session(spec)["activity_id"] == 999


# --- Garmin's odd replies ---------------------------------------------------------------


def test_garmins_no_reading_stress_codes_are_none() -> None:
    summary = StressSummary.from_garmin(
        {"avgStressLevel": -1, "maxStressLevel": -2, "stressValuesArray": None}
    )
    assert (summary.average, summary.max, summary.rest_minutes) == (None, None, None)


def test_a_create_reply_without_an_id_is_an_error(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["upload_workout"] = {}
    response = TestClient(create_app()).post(
        "/v1/workouts/strength",
        json={
            "name": "Push",
            "schedule_date": "2026-10-22",
            "exercises": [{"category": "PUSH_UP"}],
        },
    )
    assert response.status_code == 502
    assert ("schedule_workout",) not in [c[:1] for c in fake_garmin["calls"]]


def test_an_empty_sets_reply_is_not_found(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_activity_exercise_sets"] = []
    assert TestClient(create_app()).get("/v1/activities/5/sets").status_code == 404


def test_nulls_in_the_activity_list_are_skipped(fake_garmin: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(activities, "_types_cache", None)
    fake_garmin["responses"]["get_activities"] = [{"activityId": 1}, None]
    assert [a.activity_id for a in activities.list_activities()] == [1]


def test_an_impossible_epoch_reads_as_no_time() -> None:
    from garmin_api.models.common import parse_dt

    assert parse_dt(-1792300000000) is None


def test_an_odd_readiness_score_leaves_only_readiness_empty(fake_garmin: dict[str, Any]) -> None:
    """Checked as it lands, so it cannot break the schema, and it costs only its own part."""
    fake_garmin["responses"].update(
        {
            "get_stats": {"totalSteps": 9000},
            "get_sleep_data": None,
            "get_hrv_data": None,
            "get_daily_weigh_ins": None,
            "get_training_readiness": [{"score": 58.5}],
        }
    )
    response = TestClient(create_app()).get("/v1/health/day/2026-01-01")
    assert response.status_code == 200
    assert response.json()["steps"] == 9000
    assert response.json()["training_readiness"] is None


def test_a_day_where_every_part_fails_is_an_outage(fake_garmin: dict[str, Any]) -> None:
    def down(*_: Any) -> Any:
        raise GarminUnavailable("Garmin answered with a server error (503).")

    for method in (
        "get_stats",
        "get_sleep_data",
        "get_hrv_data",
        "get_training_readiness",
        "get_daily_weigh_ins",
    ):
        fake_garmin["responses"][method] = down
    assert TestClient(create_app()).get("/v1/health/day/2026-01-01").status_code == 502


@pytest.mark.parametrize(
    "path",
    [
        "/v1/health/trends/stress?start=9999-12-01&end=9999-12-31",
        "/v1/health/trends/steps?start=1900-01-01&end=1900-01-31",
    ],
)
def test_dates_far_outside_garmins_history_are_a_400(path: str) -> None:
    assert TestClient(create_app()).get(path).status_code == 400


def test_a_weigh_in_in_the_far_future_is_a_400() -> None:
    response = TestClient(create_app()).post(
        "/v1/health/weight", params={"weight": 80, "timestamp": "9999-12-31T23:59:59-12:00"}
    )
    assert response.status_code == 400


def test_a_hand_written_tool_reports_an_odd_reply_as_garmins(fake_garmin: dict[str, Any]) -> None:
    from fastmcp import Client

    from garmin_api.mcp import create_mcp

    fake_garmin["responses"].update(
        {
            "get_stats": {},
            "get_sleep_data": None,
            "get_training_readiness": None,
            # An activity name that is not a string: no tolerance covers it.
            "connectapi": [{"activityId": 1, "activityName": {"not": "a string"}}],
        }
    )

    async def call() -> Any:
        async with Client(create_mcp()) as client:
            return await client.call_tool(
                "week_summary", {"end_day": "2026-10-18"}, raise_on_error=False
            )

    result = asyncio.run(call())
    assert result.is_error
    assert "shape this package did not expect" in result.content[0].text


def test_creating_a_workout_with_no_reply_does_not_schedule(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["upload_workout"] = None
    with pytest.raises(GarminUnavailable):
        workouts.create_raw({"workoutName": "x"})

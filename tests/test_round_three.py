"""Regressions from the third review round: concurrency, journeys, and 0.2.2's own changes."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from garminconnect import GarminConnectAuthenticationError

from garmin_api import auth, catalogue
from garmin_api.errors import InvalidRequest, NotAuthenticated, RateLimited
from garmin_api.http.app import create_app
from garmin_api.models.activities import SetEdit
from garmin_api.models.workouts import StrengthSessionSpec
from garmin_api.services import activities, trends, uploads

# --- A stand-in for python-garminconnect's client --------------------------------------


class FakeGarmin:
    """Built from stored tokens, as auth.Session builds the real one."""

    built: list[FakeGarmin] = []
    refuse = False

    def __init__(self) -> None:
        self.client = SimpleNamespace(_tokenstore_path="/somewhere")
        self.calls = 0
        FakeGarmin.built.append(self)

    def login(self, tokenstore: str | None = None) -> None:
        if FakeGarmin.refuse:
            raise GarminConnectAuthenticationError("Authentication failed: API Error 401")

    def get_user_timezone(self) -> str:
        return "America/New_York"

    def whoami(self) -> str:
        self.calls += 1
        return "ok"


@pytest.fixture
def stored(no_real_tokens: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    FakeGarmin.built = []
    FakeGarmin.refuse = False
    monkeypatch.setattr(auth, "Garmin", FakeGarmin)
    (no_real_tokens / "garmin_tokens.json").write_text('{"di_token": "a"}')
    (no_real_tokens / auth.LOGIN_MARKER).write_text("login-1")
    return no_real_tokens


def test_a_running_server_notices_a_logout(stored: Path) -> None:
    session = auth.Session()
    first = session.client()
    (stored / "garmin_tokens.json").unlink()
    with pytest.raises(NotAuthenticated):
        session.client()
    assert first.client._tokenstore_path is None, "the old client must not write tokens back"


def test_a_running_server_notices_a_new_login(stored: Path) -> None:
    session = auth.Session()
    first = session.client()
    assert session.client() is first, "the same login keeps its client"
    (stored / auth.LOGIN_MARKER).write_text("login-2")
    assert session.client() is not first


def test_tokens_garmin_refused_are_not_tried_again_until_a_new_login(stored: Path) -> None:
    FakeGarmin.refuse = True
    session = auth.Session()
    for _ in range(3):
        with pytest.raises(NotAuthenticated):
            session.client()
    assert len(FakeGarmin.built) == 1
    FakeGarmin.refuse = False
    (stored / auth.LOGIN_MARKER).write_text("login-2")
    session.client()
    assert len(FakeGarmin.built) == 2


def test_a_late_401_does_not_discard_the_client_another_call_built(stored: Path) -> None:
    session = auth.Session()
    stale = session.client()
    fresh = FakeGarmin()

    def fail_after_replacement() -> str:
        session._client = fresh  # another thread rebuilt while this call was in flight
        raise GarminConnectAuthenticationError("Authentication failed: API Error 401")

    stale.whoami = fail_after_replacement  # type: ignore[method-assign]
    assert session.call("whoami") == "ok"
    assert session._client is fresh


def test_a_429_while_connecting_is_a_rate_limit_not_a_login_problem() -> None:
    cause = Exception("Too many requests")
    cause.response = SimpleNamespace(status_code=429)  # type: ignore[attr-defined]
    try:
        try:
            raise cause
        except Exception as inner:
            raise GarminConnectAuthenticationError("Authentication failed") from inner
    except GarminConnectAuthenticationError as wrapped:
        assert isinstance(auth.translate(wrapped), RateLimited)


def test_token_age_survives_a_logout_in_between(no_real_tokens: Path, monkeypatch) -> None:
    token = no_real_tokens / "garmin_tokens.json"
    token.write_text("{}")

    def gone(self: Path, *args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(Path, "stat", gone)
    assert auth.token_age_days() is None


# --- Two calls at once ------------------------------------------------------------------


def test_two_set_edits_at_once_both_stick(fake_garmin: dict[str, Any]) -> None:
    stored_rows = [
        {"setType": "ACTIVE", "repetitionCount": 5, "weight": 60000.0, "exercises": []}
        for _ in range(3)
    ]

    def read(*_: Any) -> Any:
        snapshot = [dict(row) for row in stored_rows]
        time.sleep(0.05)  # long enough for the other edit to read the same list
        return {"exerciseSets": snapshot}

    def write(_: Any, payload: dict[str, Any]) -> Any:
        stored_rows[:] = payload["exerciseSets"]
        return {}

    fake_garmin["responses"].update(
        {"get_activity_exercise_sets": read, "set_activity_exercise_sets": write}
    )
    edits = [
        SetEdit(changes=[{"sets": [1], "reps": 10}]),
        SetEdit(changes=[{"sets": [3], "weight_kg": 70}]),
    ]
    threads = [threading.Thread(target=activities.edit_sets, args=(1, e)) for e in edits]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert stored_rows[0]["repetitionCount"] == 10
    assert stored_rows[2]["weight"] == 70000.0


# --- Uploads ----------------------------------------------------------------------------

TYPES = [
    {"typeId": 17, "typeKey": "all", "parentTypeId": None},
    {"typeId": 29, "typeKey": "fitness_equipment", "parentTypeId": 17},
    {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
]
SESSION = {
    "name": "Push",
    "start_time": "2026-09-21T07:00:00+00:00",
    "exercises": [{"category": "PUSH_UP", "sets": [{"reps": 10}]}],
}
ACCEPTED = {"detailedImportResult": {"uploadId": 991, "successes": [], "failures": []}}


def _at(activity_id: int) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": "strength_training"},
        "startTimeGMT": "2026-09-21 07:00:00",
    }


def _listing(*pages: Any) -> Any:
    answers = list(pages)
    return lambda *_, **__: answers.pop(0) if len(answers) > 1 else answers[0]


@pytest.fixture
def uploading(fake_garmin: dict[str, Any], monkeypatch) -> dict[str, Any]:
    monkeypatch.setattr(uploads, "FIND_INTERVAL_S", 0.0)
    fake_garmin["responses"]["get_activity_types"] = TYPES
    return fake_garmin


def test_two_new_activities_at_once_are_listed_not_guessed(uploading: dict[str, Any]) -> None:
    uploading["responses"].update(
        {"upload_activity": ACCEPTED, "connectapi": _listing([], [_at(501), _at(502)])}
    )
    result = uploads.log_strength_session(StrengthSessionSpec.model_validate(SESSION))
    assert result["status"] == "accepted"
    assert result["candidates"] == [501, 502]


def test_still_processing_says_the_name_is_not_applied_yet(uploading: dict[str, Any]) -> None:
    uploading["responses"].update({"upload_activity": ACCEPTED, "connectapi": []})
    result = uploads.log_strength_session(StrengthSessionSpec.model_validate(SESSION))
    assert "update_activity" in result["note"]


def test_another_activity_at_the_same_second_is_pointed_out(uploading: dict[str, Any]) -> None:
    uploading["responses"].update(
        {
            "upload_activity": {"detailedImportResult": {"successes": [{"internalId": 7}]}},
            "connectapi": [_at(6)],
            "set_activity_name": {},
        }
    )
    result = uploads.log_strength_session(StrengthSessionSpec.model_validate(SESSION))
    assert result["also_at_this_time"] == [6]
    assert result["duration_s"] == pytest.approx(30.0)


def test_a_time_with_no_offset_is_the_accounts_local_time(uploading: dict[str, Any]) -> None:
    from garmin_api.fit import decode_strength_session

    sent: list[bytes] = []

    def upload(path: str) -> Any:
        sent.append(Path(path).read_bytes())
        return {"detailedImportResult": {"successes": [{"internalId": 9}]}}

    uploading["responses"].update(
        {
            "_user_tzinfo": ZoneInfo("America/New_York"),
            "upload_activity": upload,
            "connectapi": [],
            "set_activity_name": {},
        }
    )
    spec = StrengthSessionSpec.model_validate({**SESSION, "start_time": "2026-09-21T07:00:00"})
    uploads.log_strength_session(spec)
    started = decode_strength_session(sent[0]).start_time
    assert started == datetime(2026, 9, 21, 11, 0, tzinfo=UTC)
    assert started.utcoffset() == timedelta(hours=-4)


# --- Journeys ---------------------------------------------------------------------------


def test_the_summary_tools_give_local_start_times(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.mcp import create_mcp

    evening = {
        "activityId": 1,
        "activityName": "Push",
        "activityType": {"typeKey": "strength_training"},
        "startTimeGMT": "2026-10-19 00:30:00",
        "startTimeLocal": "2026-10-18 20:30:00",
    }
    fake_garmin["responses"].update({"get_activity_types": TYPES, "get_activities": [evening]})
    result = asyncio.run(
        create_mcp().call_tool("recent_strength_sessions", {"count": 1, "include_sets": False})
    )
    assert result.structured_content["result"][0]["start_time_local"].startswith("2026-10-18T20:30")


def test_a_trends_change_ignores_a_week_the_range_cuts_short(fake_garmin: dict[str, Any]) -> None:
    start, end = date(2026, 7, 19), date(2026, 8, 3)  # a Sunday stub, two whole weeks, a stub
    fake_garmin["responses"]["get_rhr_daily"] = [
        {"calendarDate": (start + timedelta(days=i)).isoformat(), "value": 40 + i}
        for i in range((end - start).days + 1)
    ]
    trend = trends.trend("resting_hr", start, end, aggregation="week")
    whole = [p for p in trend.points if p.days == 7]
    assert trend.summary["change"] == round(whole[-1].value - whole[0].value, 1)


def test_resources_can_be_listed() -> None:
    from fastmcp import Client

    from garmin_api.mcp import create_mcp

    async def listed() -> list[Any]:
        async with Client(create_mcp()) as client:
            return await client.list_resources()

    assert asyncio.run(listed())


def test_deleting_a_set_takes_its_rest_with_it(fake_garmin: dict[str, Any]) -> None:
    rows = [
        {"setType": "ACTIVE", "repetitionCount": 5, "exercises": []},
        {"setType": "REST", "duration": 60.0},
        {"setType": "ACTIVE", "repetitionCount": 6, "exercises": []},
        {"setType": "REST", "duration": 90.0},
        {"setType": "ACTIVE", "repetitionCount": 7, "exercises": []},
    ]
    sent: list[Any] = []
    fake_garmin["responses"].update(
        {
            "get_activity_exercise_sets": lambda *_: {"exerciseSets": [dict(r) for r in rows]},
            "set_activity_exercise_sets": lambda _, payload: sent.append(payload) or {},
        }
    )
    activities.edit_sets(1, SetEdit(delete=[3]))
    kinds = [(r["setType"], r.get("repetitionCount")) for r in sent[-1]["exerciseSets"]]
    assert kinds == [("ACTIVE", 5), ("REST", None), ("ACTIVE", 6)]


def test_the_cli_can_unschedule(fake_garmin: dict[str, Any]) -> None:
    from typer.testing import CliRunner

    from garmin_api.cli import app

    fake_garmin["responses"]["unschedule_workout"] = {}
    result = CliRunner().invoke(app, ["workout", "unschedule", "9001"])
    assert result.exit_code == 0, result.output
    assert ("unschedule_workout", (9001,)) in fake_garmin["calls"]


# --- 0.2.2's own regressions ----------------------------------------------------------------


@pytest.mark.parametrize("origin", ["http://localhost:3000", "https://localhost:9999"])
def test_a_portless_host_does_not_admit_other_local_pages(origin: str) -> None:
    response = TestClient(create_app()).post(
        "/v1/health/weight", params={"weight": 80}, headers={"Host": "localhost", "Origin": origin}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Push-ups", "Push-up"),
        ("Pull-ups", "Pull-up"),
        ("Sit-ups", "Sit-up"),
        ("Step-ups", "Step-up"),
    ],
)
def test_hyphenated_plurals_match_again(name: str, expected: str) -> None:
    found = catalogue.match(name)
    assert found is not None and found.name == expected


def test_a_sub_second_gap_between_sets_is_not_a_rest() -> None:
    from garmin_api.fit import decode_strength_session, encode_strength_session

    spec = StrengthSessionSpec.model_validate(
        {
            **SESSION,
            "exercises": [
                {"category": "PUSH_UP", "sets": [{"reps": 5, "duration_s": 30.4, "rest_s": 0}] * 3}
            ],
        }
    )
    decoded = decode_strength_session(encode_strength_session(spec))
    assert [s.rest_s for s in decoded.exercises[0].sets] == [0.0, 0.0, None]
    assert encode_strength_session(decoded) == encode_strength_session(spec)


def test_an_unidentified_exercise_says_it_needs_a_name() -> None:
    with pytest.raises(InvalidRequest, match="could not identify"):
        catalogue.validate("UNKNOWN", "UNKNOWN")

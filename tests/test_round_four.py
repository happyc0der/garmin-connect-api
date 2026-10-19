"""Regressions from the fourth review round: security and data integrity."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from garmin_api.errors import GarminUnavailable, InvalidRequest
from garmin_api.fit import decode_strength_session
from garmin_api.http.app import LOCAL_HOSTS, create_app
from garmin_api.models.activities import SetEdit
from garmin_api.models.workouts import StrengthSessionExercise
from garmin_api.services import activities


def test_an_applied_set_edit_is_reported_as_applied_even_if_reading_back_fails(
    fake_garmin: dict[str, Any],
) -> None:
    """An error would invite a retry, and a retried "delete set 2" deletes another set."""
    rows = [{"setType": "ACTIVE", "repetitionCount": n, "exercises": []} for n in (5, 6, 7)]
    reads = iter([{"exerciseSets": [dict(r) for r in rows]}])

    def read(*_: Any) -> Any:
        try:
            return next(reads)
        except StopIteration:
            raise GarminUnavailable("Garmin answered with a server error (503).") from None

    fake_garmin["responses"].update(
        {"get_activity_exercise_sets": read, "set_activity_exercise_sets": {}}
    )
    result = activities.edit_sets(1, SetEdit(delete=[2]))
    assert [s.reps for s in result.sets] == [5, 7]
    assert "Do not repeat" in (result.note or "")


def test_testserver_is_not_a_trusted_host(monkeypatch) -> None:
    monkeypatch.delenv("GARMIN_API_HOSTS", raising=False)
    assert "testserver" not in LOCAL_HOSTS
    response = TestClient(create_app()).patch(
        "/v1/activities/5",
        params={"name": "pwned"},
        headers={"Host": "testserver:18765", "Origin": "http://testserver:18765"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_page_on_another_site_cannot_spend_the_accounts_requests(
    site: str, fake_garmin: dict[str, Any]
) -> None:
    response = TestClient(create_app()).get(
        "/v1/health/trends/stress?start=2025-01-01&end=2026-10-01",
        headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors"},
    )
    assert response.status_code == 403
    assert fake_garmin["calls"] == []


def test_the_services_own_pages_still_work() -> None:
    response = TestClient(create_app()).get("/healthz", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"


def test_a_foreign_origin_is_refused_on_reads_too(fake_garmin: dict[str, Any]) -> None:
    response = TestClient(create_app()).get(
        "/v1/activities", headers={"Origin": "https://evil.example", "Host": "127.0.0.1:8765"}
    )
    assert response.status_code == 403


def test_a_display_name_alone_resolves_to_its_variant() -> None:
    exercise = StrengthSessionExercise.model_validate(
        {"category": "BENCH_PRESS", "name": "Incline Dumbbell Bench Press", "sets": [{"reps": 5}]}
    )
    assert exercise.exercise_name == "INCLINE_DUMBBELL_BENCH_PRESS"


def test_a_display_name_that_is_no_exercise_is_refused_not_made_generic() -> None:
    with pytest.raises(ValidationError, match="not an exercise"):
        StrengthSessionExercise.model_validate(
            {"category": "BENCH_PRESS", "name": "My favourite press", "sets": [{"reps": 5}]}
        )


def test_a_zip_that_expands_too_far_is_refused_unread() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("activity.fit", b"\0" * (40 * 1024 * 1024))
    with pytest.raises(InvalidRequest, match="too large"):
        decode_strength_session(buffer.getvalue())


# --- 0.2.3's regressions: refused tokens -------------------------------------------------


def test_a_network_failure_while_connecting_is_not_a_refusal() -> None:
    import requests
    from garminconnect import GarminConnectAuthenticationError

    from garmin_api import auth

    try:
        try:
            raise requests.ConnectionError("Network is unreachable")
        except requests.ConnectionError as inner:
            raise GarminConnectAuthenticationError("Failed to retrieve social profile") from inner
    except GarminConnectAuthenticationError as wrapped:
        assert isinstance(auth.translate(wrapped), GarminUnavailable)


def test_a_private_reply_is_withheld_data_not_a_bad_login() -> None:
    from garminconnect import GarminConnectAuthenticationError

    from garmin_api import auth
    from garmin_api.errors import Withheld

    assert isinstance(
        auth.translate(GarminConnectAuthenticationError("Authentication error")), Withheld
    )


def test_one_failed_connection_does_not_lock_the_server_out(no_real_tokens, monkeypatch) -> None:
    """0.2.3 held any failure to connect against the tokens until a restart."""
    import requests
    from garminconnect import GarminConnectAuthenticationError

    from garmin_api import auth

    (no_real_tokens / "garmin_tokens.json").write_text("{}")
    outage = {"on": True}

    class FlakyGarmin:
        def __init__(self) -> None:
            self.client = type("C", (), {"_tokenstore_path": None})()

        def login(self, tokenstore: str | None = None) -> None:
            if outage["on"]:
                try:
                    raise requests.ConnectionError("down")
                except requests.ConnectionError as inner:
                    raise GarminConnectAuthenticationError(
                        "Failed to retrieve social profile"
                    ) from inner

        def get_user_timezone(self) -> str:
            return "UTC"

        def whoami(self) -> str:
            return "me"

    monkeypatch.setattr(auth, "Garmin", FlakyGarmin)
    monkeypatch.setattr(auth, "UNREACHABLE_HOLD_S", 0)  # past the brief hold
    session = auth.Session()
    with pytest.raises(GarminUnavailable):
        session.call("whoami")
    outage["on"] = False
    assert session.call("whoami") == "me"


def test_a_401_refusal_is_held_but_heals_after_the_hold(no_real_tokens, monkeypatch) -> None:
    from types import SimpleNamespace

    from garminconnect import GarminConnectAuthenticationError

    from garmin_api import auth
    from garmin_api.errors import NotAuthenticated

    (no_real_tokens / "garmin_tokens.json").write_text("{}")
    built: list[int] = []

    class RefusedGarmin:
        def __init__(self) -> None:
            built.append(1)
            self.client = SimpleNamespace(_tokenstore_path=None)

        def login(self, tokenstore: str | None = None) -> None:
            cause = Exception("401")
            cause.response = SimpleNamespace(status_code=401)  # type: ignore[attr-defined]
            raise GarminConnectAuthenticationError("Authentication failed") from cause

    monkeypatch.setattr(auth, "Garmin", RefusedGarmin)
    session = auth.Session()
    for _ in range(3):
        with pytest.raises(NotAuthenticated):
            session.client()
    assert len(built) == 1, "refused tokens are not tried again at once"
    monkeypatch.setattr(auth, "REFUSAL_HOLD_S", -1)
    with pytest.raises(NotAuthenticated):
        session.client()
    assert len(built) == 2, "but the hold expires"


def test_an_account_timezone_that_cannot_be_read_is_an_error_not_the_machines(
    fake_garmin: dict[str, Any],
) -> None:
    from datetime import datetime

    from garmin_api import auth

    fake_garmin["responses"]["get_user_timezone"] = None
    with pytest.raises(GarminUnavailable, match="timezone"):
        auth.in_account_time(datetime(2026, 10, 19, 7, 0))


# --- The round-four sweep -------------------------------------------------------------


def test_an_odd_stats_value_costs_only_its_field(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.services import health

    fake_garmin["responses"]["get_stats"] = {"totalSteps": 9000, "restingHeartRate": 58.5}
    summary = health.day("2026-10-15", parts={"stats"})
    assert (summary.steps, summary.resting_hr) == (9000, None)


def test_a_rate_limited_connect_cools_down_instead_of_being_retried(
    no_real_tokens, monkeypatch
) -> None:
    from types import SimpleNamespace

    from garminconnect import GarminConnectTooManyRequestsError

    from garmin_api import auth
    from garmin_api.errors import RateLimited

    (no_real_tokens / "garmin_tokens.json").write_text("{}")
    built: list[int] = []

    class ThrottledGarmin:
        def __init__(self) -> None:
            built.append(1)
            self.client = SimpleNamespace(_tokenstore_path=None)

        def login(self, tokenstore: str | None = None) -> None:
            raise GarminConnectTooManyRequestsError("Rate limit exceeded")

    monkeypatch.setattr(auth, "Garmin", ThrottledGarmin)
    session = auth.Session()
    for _ in range(4):
        with pytest.raises(RateLimited):
            session.client()
    assert len(built) == 1


def test_a_rate_limit_while_labelling_stops_further_calls(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.errors import RateLimited
    from garmin_api.models.workouts import StrengthSessionSpec
    from garmin_api.services import uploads

    def limited(*_: Any) -> Any:
        raise RateLimited("Garmin is rate limiting this account.")

    fake_garmin["responses"].update(
        {
            "get_activity_types": [
                {"typeId": 13, "typeKey": "strength_training", "parentTypeId": None}
            ],
            "connectapi": [],
            "upload_activity": {"detailedImportResult": {"successes": [{"internalId": 7}]}},
            "set_activity_name": limited,
            "set_activity_description": {},
        }
    )
    spec = StrengthSessionSpec.model_validate(
        {
            "name": "Push",
            "notes": "felt strong",
            "start_time": "2026-09-21T07:00:00+00:00",
            "exercises": [{"category": "PUSH_UP", "sets": [{"reps": 5}]}],
        }
    )
    result = uploads.log_strength_session(spec)
    assert result["status"] == "created"
    assert "set_activity_description" not in [c[0] for c in fake_garmin["calls"]]
    assert "rate limiting" in result["note"]


def test_a_portless_host_takes_the_requests_own_scheme() -> None:
    response = TestClient(create_app()).post(
        "/v1/health/weight",
        params={"weight": 80},
        headers={"Host": "localhost", "Origin": "https://localhost"},
    )
    assert response.status_code == 403


def test_an_unknown_timezone_is_asked_for_again(fake_garmin: dict[str, Any], monkeypatch) -> None:
    from types import SimpleNamespace

    from garmin_api import auth

    client = SimpleNamespace(_time_zone_loaded=True)
    monkeypatch.setattr(auth.session(), "_client", client)
    fake_garmin["responses"]["get_user_timezone"] = None
    with pytest.raises(GarminUnavailable):
        auth.account_zone()
    assert client._time_zone_loaded is False


# --- Round five ------------------------------------------------------------------------


def test_withheld_stats_are_listed_not_shown_as_an_empty_day(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.errors import Withheld
    from garmin_api.services import health

    def private(*_: Any) -> Any:
        raise Withheld("Garmin marks this data private and did not return it.")

    fake_garmin["responses"].update({"get_stats": private, "get_sleep_data": None})
    summary = health.day("2026-10-15", parts={"stats", "sleep"})
    assert summary.unavailable == {"stats": "Garmin marks this data private and did not return it."}


def test_a_failed_connect_is_not_repeated_for_every_part_of_the_day(
    no_real_tokens, monkeypatch
) -> None:
    import requests
    from garminconnect import GarminConnectAuthenticationError

    from garmin_api import auth
    from garmin_api.services import health

    (no_real_tokens / "garmin_tokens.json").write_text("{}")
    connects: list[int] = []

    class DownGarmin:
        def __init__(self) -> None:
            self.client = type("C", (), {"_tokenstore_path": None})()

        def login(self, tokenstore: str | None = None) -> None:
            connects.append(1)
            try:
                raise requests.ConnectionError("down")
            except requests.ConnectionError as inner:
                raise GarminConnectAuthenticationError(
                    "Failed to retrieve social profile"
                ) from inner

    monkeypatch.setattr(auth, "Garmin", DownGarmin)
    monkeypatch.setattr(auth, "_session", auth.Session())
    with pytest.raises(GarminUnavailable):
        health.day("2026-10-15")
    assert len(connects) == 1


def test_an_upload_reply_that_cannot_be_read_is_an_error(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["upload_activity"] = {"detailedImportResult": {"processingTime": 12}}
    response = TestClient(create_app()).post(
        "/v1/sessions/upload", files={"file": ("a.fit", b"fit")}
    )
    assert response.status_code == 502
    assert "list_activities" in response.json()["detail"]


def test_a_proxied_name_accepts_its_own_https_pages(monkeypatch) -> None:
    """Behind a proxy that ends TLS, the service sees http; its /docs page is https."""
    monkeypatch.setenv("GARMIN_API_HOSTS", "garmin.example")
    response = TestClient(create_app()).post(
        "/v1/health/weight",
        params={"weight": 80},
        headers={"Host": "garmin.example", "Origin": "https://garmin.example"},
    )
    assert response.status_code != 403


def test_the_body_fat_trend_keeps_a_day_whose_last_weigh_in_has_none(
    fake_garmin: dict[str, Any],
) -> None:
    from garmin_api.services import trends

    fake_garmin["responses"]["get_weigh_ins"] = {
        "dailyWeightSummaries": [
            {
                "summaryDate": "2026-10-15",
                "latestWeight": {"weight": 72000, "bodyFat": None, "timestampGMT": 2},
                "allWeightMetrics": [
                    {"weight": 72400, "bodyFat": 18.4, "timestampGMT": 1},
                    {"weight": 72000, "bodyFat": None, "timestampGMT": 2},
                ],
            }
        ]
    }
    trend = trends.trend("body_fat", "2026-10-15", "2026-10-15")
    assert [p.value for p in trend.points] == [18.4]


def test_a_laps_pace_and_its_summary_agree(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.models.laps import build

    splits = {
        "lapDTOs": [
            {"lapIndex": 1, "distance": 1000, "duration": 400, "movingDuration": 300},
            {"lapIndex": 2, "distance": 1000, "duration": 330, "movingDuration": 330},
        ]
    }
    laps = build(1, "running", splits)
    assert [lap.pace for lap in laps.laps] == ["5:00/km", "5:30/km"]
    assert laps.summary["fastest_lap"] == 1


def test_the_halves_split_the_distance_not_the_laps() -> None:
    from garmin_api.models.laps import build

    splits = {
        "lapDTOs": [
            {"lapIndex": 1, "distance": 9000, "duration": 2700, "movingDuration": 2700},
            {"lapIndex": 2, "distance": 1000, "duration": 420, "movingDuration": 420},
        ]
    }
    summary = build(1, "running", splits).summary
    assert (summary["first_half_pace"], summary["second_half_pace"]) == ("5:00/km", "5:24/km")

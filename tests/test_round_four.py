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

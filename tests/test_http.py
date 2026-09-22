"""The HTTP service.

Two things matter here beyond the routing: that a caller without tokens is
told what to do rather than given a stack trace, and that the operation ids
stay stable, because they become the MCP tool names.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from garmin_api.errors import RateLimited
from garmin_api.http.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_healthz_needs_no_login(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


def test_exercise_search_works_logged_out(client: TestClient) -> None:
    """The catalogue ships with the package, so it needs neither net nor login."""
    response = client.get("/v1/exercises/search", params={"q": "bench press"})
    assert response.status_code == 200
    names = [row["exercise"] for row in response.json()]
    assert "BARBELL_BENCH_PRESS" in names


def test_missing_tokens_answer_401_with_instructions(client: TestClient) -> None:
    response = client.get("/v1/activities")
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "NotAuthenticated"
    assert "garmin login" in (body["detail"] or "")


def test_rate_limiting_surfaces_as_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 must be visible as a 429, so a caller knows to stop rather than retry."""

    def explode(*_: Any, **__: Any) -> Any:
        raise RateLimited("Garmin is rate limiting this account.", detail="Wait.")

    monkeypatch.setattr("garmin_api.services.activities.auth.call", explode)
    response = client.get("/v1/activities")
    assert response.status_code == 429
    assert response.json()["error"] == "RateLimited"


def test_an_unknown_exercise_is_a_400_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "garmin_api.services.workouts.auth.call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not reach Garmin")),
    )
    response = client.post(
        "/v1/workouts/strength",
        json={
            "name": "Nope",
            "exercises": [{"category": "BENCH_PRESS", "exercise_name": "NOT_A_THING"}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "InvalidRequest"


def test_encoding_a_session_returns_a_fit_file(client: TestClient) -> None:
    response = client.post(
        "/v1/sessions/strength/encode",
        json={
            "name": "Push A",
            "start_time": "2026-09-21T07:00:00+00:00",
            "exercises": [
                {
                    "category": "BENCH_PRESS",
                    "exercise_name": "BARBELL_BENCH_PRESS",
                    "sets": [{"reps": 10, "weight_kg": 60}],
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.content[8:12] == b".FIT"


def test_encode_then_decode_over_http(client: TestClient) -> None:
    payload = {
        "name": "Push A",
        "start_time": "2026-09-21T07:00:00+00:00",
        "exercises": [
            {
                "category": "BENCH_PRESS",
                "exercise_name": "BARBELL_BENCH_PRESS",
                "sets": [{"reps": 10, "weight_kg": 60}, {"reps": 8, "weight_kg": 65}],
            }
        ],
    }
    encoded = client.post("/v1/sessions/strength/encode", json=payload)
    decoded = client.post(
        "/v1/sessions/strength/decode",
        files={"file": ("session.fit", encoded.content, "application/octet-stream")},
    )
    assert decoded.status_code == 200
    body = decoded.json()
    assert len(body["exercises"][0]["sets"]) == 2
    assert body["exercises"][0]["sets"][0]["weight_kg"] == 60.0


def test_api_key_is_enforced_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARMIN_API_KEY", "secret")
    guarded = TestClient(create_app(), raise_server_exceptions=False)

    assert guarded.get("/v1/exercises/categories").status_code == 401
    assert guarded.get("/healthz").status_code == 200, "health checks stay open"

    allowed = guarded.get("/v1/exercises/categories", headers={"Authorization": "Bearer secret"})
    assert allowed.status_code == 200


def test_operation_ids_are_unique_and_stable(client: TestClient) -> None:
    """These become MCP tool names, so duplicates or churn break callers."""
    spec = client.get("/openapi.json").json()
    ids = [op["operationId"] for path in spec["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids))

    expected = {
        "list_activities",
        "get_activity_sets",
        "log_strength_session",
        "create_strength_workout",
        "schedule_workout",
        "get_day_summary",
        "search_exercises",
    }
    assert expected <= set(ids)

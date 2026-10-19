"""Regression tests for the independent code review of 19 Oct 2026."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from garminconnect import GarminConnectConnectionError

from garmin_api import auth
from garmin_api.errors import Duplicate, NotAuthenticated, RateLimited
from garmin_api.http.app import create_app

STRENGTH_ROW = {
    "activityId": 1,
    "activityName": "Push A",
    "activityType": {"typeKey": "strength_training"},
    "startTimeGMT": "2026-09-21 05:00:00",
}


# --- Error translation on writes -------------------------------------------


def test_a_429_on_a_write_still_means_stop() -> None:
    """Writes bypass the library's error mapper, so the status arrives raw."""
    exc = GarminConnectConnectionError("boom")
    exc.response = SimpleNamespace(status_code=429)
    assert isinstance(auth.translate(exc), RateLimited)


def test_a_401_on_a_write_means_log_in() -> None:
    exc = GarminConnectConnectionError("boom")
    exc.response = SimpleNamespace(status_code=401)
    assert isinstance(auth.translate(exc), NotAuthenticated)


def test_the_status_is_found_through_the_cause_chain() -> None:
    """upload_activity re-raises as "Import error: ..." from the original."""
    original = GarminConnectConnectionError("transport")
    original.response = SimpleNamespace(status_code=429)
    try:
        try:
            raise original
        except GarminConnectConnectionError as inner:
            raise GarminConnectConnectionError("Import error: transport") from inner
    except GarminConnectConnectionError as wrapped:
        assert isinstance(auth.translate(wrapped), RateLimited)


def test_a_duplicate_upload_is_reported_as_one(tmp_path: Path, monkeypatch) -> None:
    from garmin_api.services import uploads

    exc = GarminConnectConnectionError("Activity already exists (duplicate): session.fit")
    assert isinstance(auth.translate(exc), Duplicate)

    def duplicate(*_: Any) -> Any:
        raise auth.translate(exc)

    monkeypatch.setattr(uploads.auth, "call", duplicate)
    f = tmp_path / "session.fit"
    f.write_bytes(b"x")
    assert uploads.upload_file(f)["status"] == "duplicate"


# --- The 401 retry uses the rebuilt client ----------------------------------


def test_the_retry_goes_out_on_the_new_client(monkeypatch) -> None:
    class Stale:
        def get_stats(self, *_: Any) -> Any:
            error = Exception("401 Unauthorized")
            error.response = SimpleNamespace(status_code=401)  # type: ignore[attr-defined]
            raise error

    class Fresh:
        def get_stats(self, *_: Any) -> Any:
            return {"ok": True}

    clients = iter([Stale(), Fresh()])
    session = auth.Session()
    monkeypatch.setattr(session, "client", lambda: session._client or _next(session, clients))
    assert session.call("get_stats", "2026-10-18") == {"ok": True}


def _next(session: auth.Session, clients: Any) -> Any:
    session._client = next(clients)
    return session._client


# --- Upload path safety -----------------------------------------------------


@pytest.mark.parametrize("name", ["../../evil.fit", "/etc/evil.fit", "nested/dir/evil.fit"])
def test_an_upload_cannot_write_outside_its_temp_dir(name: str, monkeypatch) -> None:
    from garmin_api.services import uploads

    seen: list[Path] = []
    monkeypatch.setattr(uploads, "upload_file", lambda p: seen.append(Path(p)) or {"status": "ok"})
    response = TestClient(create_app()).post(
        "/v1/sessions/upload", files={"file": (name, b"data", "application/octet-stream")}
    )
    assert response.status_code == 200
    assert seen[0].name == "evil.fit"
    assert seen[0].parent.name.startswith("tmp"), "written inside the temp dir only"


# --- Cross-origin, host and key checks --------------------------------------


def test_a_web_page_cannot_write() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post(
        "/v1/health/weight", params={"weight": 80}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_a_page_on_the_service_itself_can_write(monkeypatch) -> None:
    from garmin_api.services import weight

    monkeypatch.setattr(weight, "add_weigh_in", lambda *a, **k: {})
    client = TestClient(create_app())
    response = client.post(
        "/v1/health/weight", params={"weight": 80}, headers={"Origin": "http://127.0.0.1:8765"}
    )
    assert response.status_code == 200


def test_a_rebound_host_name_is_refused() -> None:
    """DNS rebinding: a hostile name that resolves to 127.0.0.1."""
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/healthz", headers={"Host": "attacker.example"})
    assert response.status_code == 403


def test_extra_hosts_can_be_allowed(monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_API_HOSTS", "garmin.lan")
    client = TestClient(create_app())
    assert client.get("/healthz", headers={"Host": "garmin.lan:8765"}).status_code == 200


def test_the_key_check_is_case_insensitive_and_spares_the_docs(monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_API_KEY", "secret")
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.get("/redoc").status_code == 200
    assert client.get("/v1/exercises/categories").status_code == 401
    ok = client.get("/v1/exercises/categories", headers={"Authorization": "bearer secret"})
    assert ok.status_code == 200
    wrong = client.get("/v1/exercises/categories", headers={"Authorization": "Bearer secreT"})
    assert wrong.status_code == 401


def test_generated_mcp_tools_work_when_a_key_is_set(monkeypatch) -> None:
    """They call the app in-process, so they must carry the key too."""
    from garmin_api.mcp import create_mcp

    monkeypatch.setenv("GARMIN_API_KEY", "secret")
    result = asyncio.run(create_mcp().call_tool("search_exercises", {"q": "row", "limit": 1}))
    assert result.structured_content


# --- Rate limits stop multi-call tools --------------------------------------


def test_recent_strength_sessions_stops_on_a_rate_limit(fake_garmin: dict[str, Any]) -> None:
    from fastmcp.exceptions import ToolError

    from garmin_api.mcp import create_mcp

    def throttled(*_: Any) -> Any:
        raise RateLimited("Garmin is rate limiting this account.")

    fake_garmin["responses"].update(
        {
            "get_activity_types": [
                {"typeId": 17, "typeKey": "all", "parentTypeId": None},
                {"typeId": 29, "typeKey": "fitness_equipment", "parentTypeId": 17},
                {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
            ],
            "get_activities": [STRENGTH_ROW, {**STRENGTH_ROW, "activityId": 2}],
            "get_activity_exercise_sets": throttled,
        }
    )
    from garmin_api.services import activities

    activities._types_cache = None
    with pytest.raises(ToolError, match="rate limiting"):
        asyncio.run(create_mcp().call_tool("recent_strength_sessions", {"count": 5}))
    set_calls = [n for n, _ in fake_garmin["calls"] if n == "get_activity_exercise_sets"]
    assert len(set_calls) == 1, "no second call into a rate limit"


# --- Today, devices ---------------------------------------------------------


def test_week_summary_uses_garmins_today(fake_garmin: dict[str, Any]) -> None:
    from garmin_api.mcp import create_mcp

    fake_garmin["responses"].update(
        {
            "today": date(2026, 10, 18),
            "get_stats": {},
            "get_sleep_data": None,
            "get_training_readiness": None,
            "connectapi": [],
        }
    )
    result = asyncio.run(create_mcp().call_tool("week_summary", {}))
    assert result.structured_content["to"] == "2026-10-18"

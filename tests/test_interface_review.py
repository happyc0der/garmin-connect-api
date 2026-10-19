"""Regressions from the HTTP, MCP, CLI and catalogue review of 19 Oct 2026."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from garmin_api import catalogue
from garmin_api.cli import app as cli
from garmin_api.errors import NotAuthenticated
from garmin_api.http.app import create_app
from garmin_api.mcp import create_mcp

SPEC = {
    "name": "Café ×5",
    "start_time": "2026-09-21T07:00:00-04:00",
    "exercises": [{"category": "PUSH_UP", "sets": [{"reps": 10}]}],
}
runner = CliRunner()


# --- Catalogue ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Seated Leg Curl", "Single Arm Dumbbell Row", "Lying Leg Curl", "Ab Crunch"]
)
def test_match_never_turns_one_lift_into_another(name: str) -> None:
    """A one-word change is a different exercise, however close the spelling."""
    found = catalogue.match(name)
    assert found is None or set(catalogue._words(name)) <= set(catalogue._words(found.name))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Romainian Deadlift", "Romanian Deadlift"),
        ("barbel bench press", "Barbell Bench Press"),
        ("Hack Squat", "Barbell Hack Squat"),
    ],
)
def test_match_still_forgives_a_typo_or_finds_the_only_candidate(name: str, expected: str) -> None:
    found = catalogue.match(name)
    assert found is not None and found.name == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("bench presses", "Bench Press"),
        ("crunches", "Crunch"),
        ("dumbbell flyes", "Dumbbell Fly"),
        ("lat pulldowns", "Lat Pull-down"),
        ("pushups", "Push-up"),
        ("chinups", "Chin-up"),
        ("rdls", "Romanian Deadlift"),
    ],
)
def test_search_understands_plurals(query: str, expected: str) -> None:
    assert catalogue.search(query, limit=1)[0].name == expected


def test_aliases_work_in_the_plural_and_inside_a_longer_name() -> None:
    assert catalogue.search("skull crushers")
    assert "Romanian Deadlift" in catalogue.search("dumbbell rdl")[0].name


def test_a_negative_limit_returns_nothing() -> None:
    assert catalogue.search("press", limit=-1) == []


# --- MCP ---------------------------------------------------------------------


def _tools() -> dict[str, Any]:
    return {t.name: t for t in asyncio.run(create_mcp().list_tools())}


def test_editing_sets_is_not_advertised_as_idempotent() -> None:
    """Deleting set 2 twice deletes two different sets."""
    tools = _tools()
    assert tools["edit_activity_sets"].annotations.idempotent_hint is False
    assert tools["update_activity"].annotations.idempotent_hint is True


def test_an_error_shown_as_text_carries_what_to_do() -> None:
    error = NotAuthenticated("No Garmin tokens found.", detail="Run `garmin login` first.")
    assert str(error) == "No Garmin tokens found. Run `garmin login` first."


def test_mcp_over_http_requires_the_key_when_one_is_set(monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_API_KEY", "k3y")
    http_app = create_mcp().http_app()
    with TestClient(http_app, base_url="http://127.0.0.1:8000") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        headers = {"Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", json=body, headers=headers).status_code == 401
        keyed = {**headers, "Authorization": "Bearer k3y"}
        assert client.post("/mcp", json=body, headers=keyed).status_code != 401


def test_mcp_over_http_refuses_another_site(monkeypatch) -> None:
    http_app = create_mcp().http_app()
    with TestClient(http_app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 403


def test_mcp_over_http_will_not_bind_beyond_localhost_without_a_key(monkeypatch) -> None:
    monkeypatch.delenv("GARMIN_API_KEY", raising=False)
    result = runner.invoke(cli, ["mcp", "--http", "--host", "0.0.0.0"])
    assert result.exit_code == 2
    assert "Refusing to bind beyond localhost" in result.output


# --- HTTP --------------------------------------------------------------------


def test_a_rejected_upload_is_an_error(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["upload_activity"] = {
        "detailedImportResult": {
            "failures": [
                {"messages": [{"content": "Invalid file. The file could not be processed."}]}
            ]
        }
    }
    response = TestClient(create_app()).post(
        "/v1/sessions/upload", files={"file": ("bad.fit", b"not a fit file")}
    )
    assert response.status_code == 400
    assert "could not be processed" in response.json()["detail"]


def test_an_unwritable_client_filename_is_tamed(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["upload_activity"] = {
        "detailedImportResult": {"successes": [{"internalId": 9}], "failures": []}
    }
    name = "x" * 300 + ":?.fit"
    response = TestClient(create_app()).post("/v1/sessions/upload", files={"file": (name, b"fit")})
    assert response.status_code == 200, response.text


# --- CLI ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "utf-16"], ids=["plain", "with-bom", "powershell"]
)
def test_spec_files_are_read_whatever_the_editor_saved(tmp_path: Path, encoding: str) -> None:
    spec = tmp_path / "session.json"
    spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding=encoding)
    out = tmp_path / "session.fit"
    result = runner.invoke(cli, ["encode-session", str(spec), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.stat().st_size > 0


def test_output_into_a_directory_uses_the_default_name(tmp_path: Path) -> None:
    spec = tmp_path / "session.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    result = runner.invoke(cli, ["encode-session", str(spec), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "session.fit").is_file()


def test_output_to_a_missing_directory_is_a_message_not_a_traceback(tmp_path: Path) -> None:
    spec = tmp_path / "session.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    out = tmp_path / "nowhere" / "x.fit"
    result = runner.invoke(cli, ["encode-session", str(spec), "--out", str(out)])
    assert result.exit_code == 1
    assert not isinstance(result.exception, OSError)


def test_a_rejected_upload_exits_1(tmp_path: Path, fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["upload_activity"] = {
        "detailedImportResult": {"failures": [{"messages": [{"content": "Invalid file."}]}]}
    }
    bad = tmp_path / "bad.fit"
    bad.write_bytes(b"x")
    result = runner.invoke(cli, ["upload", str(bad)])
    assert result.exit_code == 1
    assert '"rejected"' in result.output


def test_tracebacks_never_show_local_variables() -> None:
    assert cli.pretty_exceptions_show_locals is False

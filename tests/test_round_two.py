"""Fixes from the second round of live testing, on 19 Oct 2026."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from garmin_api import catalogue
from garmin_api.cli import app
from garmin_api.models.workouts import StrengthSessionSpec

runner = CliRunner()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("lat pulldown", "Lat Pull-down"),
        ("pull up", "Pull-up"),
        ("pullup", "Pull-up"),
        ("pull ups", "Pull-up"),
        ("chin up", "Chin-up"),
        ("dips", "Bench Dip"),
        ("rdl", "Romanian Deadlift"),
        ("skull crusher", "Cable Lying Triceps Extension"),
        ("ohp", "Shoulder Press"),
        ("bench press", "Bench Press"),
    ],
)
def test_common_gym_names_find_garmins_exercise(query: str, expected: str) -> None:
    assert catalogue.search(query, limit=1)[0].name == expected


def test_a_short_query_does_not_match_inside_a_word() -> None:
    """ "rdl" once matched "Hanging Hurdle", and "chin" matched "Machine"."""
    assert all("Hurdle" not in e.name for e in catalogue.search("rdl"))
    assert all("Machine" not in e.name for e in catalogue.search("chin"))


def test_a_session_without_a_timezone_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        StrengthSessionSpec(
            start_time=datetime(2026, 9, 21, 7, 0),
            exercises=[{"category": "BENCH_PRESS", "sets": [{"reps": 5}]}],
        )


def test_cli_reports_a_missing_file_without_a_traceback(tmp_path: Path) -> None:
    result = runner.invoke(app, ["decode-fit", str(tmp_path / "nope.fit")])
    assert result.exit_code == 1
    assert "No such file" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_an_invalid_spec_plainly(tmp_path: Path) -> None:
    spec = tmp_path / "bad.json"
    spec.write_text(json.dumps({"name": "x", "start_time": "2026-09-21T07:00:00", "exercises": []}))
    result = runner.invoke(app, ["encode-session", str(spec), "--out", str(tmp_path / "x.fit")])
    assert result.exit_code == 1
    assert "not valid" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_a_non_strength_file_plainly(tmp_path: Path) -> None:
    from tests.test_fit_codec import encode_minimal_non_strength

    path = tmp_path / "run.fit"
    path.write_bytes(encode_minimal_non_strength())
    result = runner.invoke(app, ["decode-fit", str(path)])
    assert result.exit_code == 1
    assert "No strength sets" in result.output


def test_logout_leaves_other_tools_files_alone(no_real_tokens: Path) -> None:
    """The token directory can be shared through GARMINTOKENS."""
    (no_real_tokens / "garmin_tokens.json").write_text("{}")
    (no_real_tokens / "other_tool_settings.json").write_text("{}")
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0
    assert not (no_real_tokens / "garmin_tokens.json").exists()
    assert (no_real_tokens / "other_tool_settings.json").exists()


@pytest.mark.parametrize(
    ("fmt", "media"),
    [
        ("fit", "application/zip"),
        ("tcx", "application/vnd.garmin.tcx+xml"),
        ("gpx", "application/gpx+xml"),
        ("csv", "text/csv"),
    ],
)
def test_downloads_say_what_they_are(fmt: str, media: str, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from garmin_api.http.app import create_app
    from garmin_api.services import activities

    monkeypatch.setattr(activities, "download", lambda *_a, **_k: b"data")
    response = TestClient(create_app()).get(f"/v1/activities/1/file?fmt={fmt}")
    assert response.headers["content-type"].startswith(media)

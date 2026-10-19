"""Shared fixtures.

Nothing in the offline suite touches the network or the real token store, and
``no_real_tokens`` enforces that: it points the token directory at a temporary
path for every test, so a developer's own Garmin login can never be read or
overwritten by a test run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from garmin_api import auth
from garmin_api.models.workouts import (
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_real_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tokens = tmp_path / "tokens"
    tokens.mkdir()
    monkeypatch.setenv("GARMINTOKENS", str(tokens))
    auth.session().reset()
    # Lookups cached for a process must not leak from one test into the next.
    from garmin_api.services import activities, health

    monkeypatch.setattr(activities, "_types_cache", None)
    monkeypatch.setattr(health, "_record_types", None)
    return tokens


@pytest.fixture
def fake_garmin(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace Garmin calls with canned responses.

    ``auth.call`` is the single door every service goes through, so stubbing
    it covers the whole package without mocking HTTP.
    """
    responses: dict[str, Any] = {}
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def fake_call(name: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((name, args))
        if name not in responses:
            raise AssertionError(f"Unexpected Garmin call: {name}")
        value = responses[name]
        return value(*args, **kwargs) if callable(value) else value

    monkeypatch.setattr(auth, "call", fake_call)
    for module in ("activities", "health", "weight", "workouts", "uploads"):
        monkeypatch.setattr(f"garmin_api.services.{module}.auth.call", fake_call, raising=False)

    return {"responses": responses, "calls": calls}


@pytest.fixture
def sample_session() -> StrengthSessionSpec:
    return StrengthSessionSpec(
        name="Push A",
        start_time=datetime(2026, 9, 21, 7, 0, tzinfo=UTC),
        exercises=[
            StrengthSessionExercise(
                category="BENCH_PRESS",
                exercise_name="BARBELL_BENCH_PRESS",
                sets=[
                    StrengthSessionSet(reps=10, weight_kg=60.0, rest_s=120),
                    StrengthSessionSet(reps=8, weight_kg=65.0, rest_s=120),
                    StrengthSessionSet(reps=6, weight_kg=70.0, rest_s=90),
                ],
            ),
            StrengthSessionExercise(
                category="ROW",
                exercise_name="BARBELL_ROW",
                sets=[StrengthSessionSet(reps=12, weight_kg=40.0, rest_s=90)],
            ),
        ],
    )


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def non_strength_fit() -> bytes:
    """A valid FIT file with no set messages, as a run or ride would be.

    A fixture rather than a function imported from a test module: importing
    ``tests.something`` works only when the repo root is on sys.path, which
    ``python -m pytest`` arranges and plain ``pytest`` does not.
    """
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.profile_type import FileType

    builder = FitFileBuilder(auto_define=True)
    message = FileIdMessage()
    message.type = FileType.ACTIVITY
    message.serial_number = 1
    message.time_created = round(datetime(2026, 9, 21, tzinfo=UTC).timestamp() * 1000)
    builder.add(message)
    return bytes(builder.build().to_bytes())

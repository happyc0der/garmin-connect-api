"""Write checks against the real account.

Each cleans up after itself. They need GARMIN_LIVE=1 and, because they create
real data, GARMIN_LIVE_WRITE=1 as well: an accidental run should not leave
junk in someone's training history.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from garmin_api import auth
from garmin_api.models.workouts import (
    StrengthExerciseSpec,
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
    StrengthWorkoutSpec,
)
from garmin_api.services import activities, uploads, workouts


@pytest.fixture(autouse=True)
def require_write_opt_in() -> None:
    if not auth.has_tokens():
        pytest.skip("No tokens. Run `garmin login` first.")
    if os.getenv("GARMIN_LIVE_WRITE") != "1":
        pytest.skip("Set GARMIN_LIVE_WRITE=1 to create and delete real Garmin data.")


def test_create_schedule_and_delete_a_workout() -> None:
    spec = StrengthWorkoutSpec(
        name="garmin-connect-api test workout",
        exercises=[
            StrengthExerciseSpec(
                category="BENCH_PRESS",
                exercise_name="BARBELL_BENCH_PRESS",
                sets=3,
                reps=8,
                weight_kg=60,
                rest_s=90,
            )
        ],
    )
    created = workouts.create_strength(spec)
    assert created.workout_id

    try:
        workouts.schedule(created.workout_id, (datetime.now(UTC) + timedelta(days=1)).date())
        fetched = workouts.get_workout(created.workout_id)
        assert fetched.name == spec.name
    finally:
        workouts.delete(created.workout_id)


def test_log_a_strength_session_and_delete_it() -> None:
    start = datetime.now(UTC) - timedelta(days=1)
    spec = StrengthSessionSpec(
        name="garmin-connect-api test session",
        start_time=start,
        exercises=[
            StrengthSessionExercise(
                category="BENCH_PRESS",
                exercise_name="BARBELL_BENCH_PRESS",
                sets=[
                    StrengthSessionSet(reps=10, weight_kg=60.0, rest_s=90),
                    StrengthSessionSet(reps=8, weight_kg=65.0, rest_s=90),
                ],
            )
        ],
    )

    result = uploads.log_strength_session(spec)
    assert result["status"] in {"created", "duplicate"}
    activity_id = result["activity_id"]
    assert activity_id, result

    try:
        # Garmin processes an upload asynchronously, so the sets may lag a
        # moment behind the activity appearing.
        found = activities.get_sets(activity_id)
        assert [s.reps for s in found.sets] == [10, 8]
        assert found.total_volume_kg == pytest.approx(10 * 60 + 8 * 65, abs=1)
    finally:
        activities.delete(activity_id)

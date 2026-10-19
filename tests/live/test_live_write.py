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
        day = (datetime.now(UTC) + timedelta(days=1)).date()
        entry = workouts.schedule(created.workout_id, day)
        assert entry.schedule_id, "unschedule needs this id"

        on_calendar = workouts.scheduled(day.year, day.month)
        assert entry.schedule_id in {e.schedule_id for e in on_calendar}

        workouts.unschedule(entry.schedule_id)
        after = workouts.scheduled(day.year, day.month)
        assert entry.schedule_id not in {e.schedule_id for e in after}

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

    activity_id: int | None = None
    try:
        result = uploads.log_strength_session(spec)
        activity_id = result["activity_id"]
        assert result["status"] in {"created", "duplicate"}, result
        assert activity_id, result
        assert result.get("name") == spec.name, "renamed after upload"

        found = activities.get_sets(activity_id)
        assert [s.reps for s in found.sets] == [10, 8]
        assert [s.weight_kg for s in found.sets] == [60.0, 65.0]
        assert found.total_volume_kg == pytest.approx(10 * 60 + 8 * 65, abs=1)
    finally:
        # Clean up even when an assertion fails: find it by its start time.
        activity_id = activity_id or uploads.find_uploaded_session(spec)
        if activity_id:
            activities.delete(activity_id)


def test_record_and_delete_a_body_composition_weigh_in() -> None:
    """Dated 2020 so a test weigh-in never sits in a recent weight trend."""
    from datetime import timezone

    from garmin_api.models.health import BodyCompositionSpec
    from garmin_api.services import weight

    moment = datetime(2020, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    spec = BodyCompositionSpec(
        timestamp=moment,
        weight_kg=72.4,
        percent_fat=18.4,
        percent_hydration=55.2,
        muscle_mass_kg=34.25,
        bone_mass_kg=3.1,
        metabolic_age=24,
    )
    result = weight.add_body_composition(spec)
    assert result["status"] == "processed", result

    found = [w for w in weight.day_weigh_ins(moment.date()) if w.weight_kg == 72.4]
    try:
        assert found, "the weigh-in should be readable straight back"
        entry = found[0]
        assert entry.body_fat_pct == pytest.approx(18.4, abs=0.1)
        assert entry.muscle_mass_kg == pytest.approx(34.25, abs=0.05)
        assert entry.metabolic_age == 24
    finally:
        for w in found:
            if w.sample_pk:
                weight.delete_weigh_in(str(w.sample_pk), moment.date())

    assert not [w for w in weight.day_weigh_ins(moment.date()) if w.weight_kg == 72.4]


def test_edit_the_sets_of_a_logged_session() -> None:
    """Upload a session, correct its sets, check Garmin kept the correction, delete it.

    Like the session test, this creates a real activity for a few seconds; if
    the account shares to Strava, turn that off first or expect a post there.
    """
    from garmin_api.models.activities import SetEdit

    start = datetime.now(UTC) - timedelta(days=2)
    spec = StrengthSessionSpec(
        name="garmin-connect-api edit test",
        start_time=start,
        exercises=[
            StrengthSessionExercise(
                category="CURL",
                exercise_name="DUMBBELL_BICEPS_CURL",
                sets=[StrengthSessionSet(reps=10, weight_kg=10.0, rest_s=60) for _ in range(3)],
            )
        ],
    )
    activity_id: int | None = None
    try:
        activity_id = uploads.log_strength_session(spec)["activity_id"]
        assert activity_id

        edited = activities.edit_sets(
            activity_id,
            SetEdit(
                changes=[
                    {
                        "sets": [2, 3],
                        "category": "ROW",
                        "exercise_name": "DUMBBELL_ROW",
                        "weight_kg": 14.0,
                    }
                ],
            ),
        )
        assert [s.exercise_name for s in edited.sets] == [
            "DUMBBELL_BICEPS_CURL",
            "DUMBBELL_ROW",
            "DUMBBELL_ROW",
        ]
        assert [s.weight_kg for s in edited.sets] == [10.0, 14.0, 14.0]

        after_delete = activities.edit_sets(activity_id, SetEdit(delete=[1]))
        remaining = [s.exercise_name for s in after_delete.sets]
        assert remaining == ["DUMBBELL_ROW", "DUMBBELL_ROW"]
    finally:
        activity_id = activity_id or uploads.find_uploaded_session(spec)
        if activity_id:
            activities.delete(activity_id)


def test_push_a_workout_to_a_real_device_and_delete_it() -> None:
    """Push a workout with no device named, and check Garmin queued it for a listed device.

    Garmin's last-used device can be a "Default" pseudo-device left by an
    upload; 0.2.0 pushed there. The workout is deleted at the end, so the watch
    never receives it; to see one arrive, push a workout of your own and sync.
    """
    spec = StrengthWorkoutSpec(
        name="garmin-connect-api push test",
        exercises=[
            StrengthExerciseSpec(category="PUSH_UP", exercise_name="PUSH_UP", sets=1, reps=5)
        ],
    )
    created = workouts.create_strength(spec)
    try:
        target = workouts.push_to_device(created.workout_id)
        assert target.device_id in {d.device_id for d in workouts.devices()}

        queue = (auth.call("get_device_messages") or {}).get("messages") or []
        ours = [
            m
            for m in queue
            if (m.get("metaData") or {}).get("metaDataId") == int(created.workout_id)
        ]
        assert ours, "Garmin should have queued the workout"
        assert {m.get("deviceId") for m in ours} == {target.device_id}
        print(f"queued for {target.name} ({target.device_id}): {ours[0].get('messageStatus')}")
    finally:
        workouts.delete(created.workout_id)

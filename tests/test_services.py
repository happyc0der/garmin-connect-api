"""Services, against canned Garmin responses.

The payload shapes here are Garmin's real ones: camelCase, grams, epoch
milliseconds, exercises nested in a ranked list. They are what the mapping
layer exists to tame.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from garmin_api.errors import InvalidRequest, NotFound
from garmin_api.services import activities, health, uploads, weight, workouts

ACTIVITY_ROW = {
    "activityId": 987654321,
    "activityName": "Push A",
    "activityType": {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
    "startTimeGMT": "2026-09-21 05:00:00",
    "startTimeLocal": "2026-09-21 07:00:00",
    "duration": 3600.0,
    "calories": 320.0,
    "averageHR": 108.0,
}

SETS_PAYLOAD = {
    "exerciseSets": [
        {
            "setType": "ACTIVE",
            "repetitionCount": 10,
            "weight": 60000.0,
            "duration": 30.0,
            "startTime": "2026-09-21T05:00:00.0",
            "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}],
        },
        {"setType": "REST", "duration": 120.0, "exercises": []},
        {
            "setType": "ACTIVE",
            "repetitionCount": 8,
            "weight": 65000.0,
            "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}],
        },
    ]
}


class TestActivities:
    def test_list_maps_garmins_field_names(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activities"] = [ACTIVITY_ROW]
        found = activities.list_activities(limit=5)

        assert len(found) == 1
        only = found[0]
        assert only.activity_id == 987654321
        assert only.name == "Push A"
        assert only.activity_type is not None
        assert only.activity_type.type_key == "strength_training"
        assert only.start_time == datetime(2026, 9, 21, 5, 0, tzinfo=UTC)

    def test_a_date_range_uses_the_other_endpoint(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activities_by_date"] = [ACTIVITY_ROW]
        activities.list_activities(start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))

        name, args = fake_garmin["calls"][0]
        assert name == "get_activities_by_date"
        assert args[0] == "2026-09-01"
        assert args[1] == "2026-09-30"

    def test_sets_drop_rests_and_convert_grams(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activity_exercise_sets"] = SETS_PAYLOAD
        result = activities.get_sets(1)

        assert len(result.sets) == 2, "the rest period should not count as a set"
        assert result.sets[0].weight_kg == 60.0
        assert result.sets[0].exercise_name == "BARBELL_BENCH_PRESS"

    def test_volume_is_reps_times_weight(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activity_exercise_sets"] = SETS_PAYLOAD
        result = activities.get_sets(1)
        assert result.total_volume_kg == pytest.approx(10 * 60 + 8 * 65)

    def test_rests_can_be_asked_for(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activity_exercise_sets"] = SETS_PAYLOAD
        result = activities.get_sets(1, include_rests=True)
        assert len(result.sets) == 3

    def test_a_missing_activity_is_not_found(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activity"] = None
        with pytest.raises(NotFound):
            activities.get_activity(42)


class TestHealth:
    def test_a_day_assembles_several_endpoints(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"].update(
            {
                "get_stats": {
                    "totalSteps": 8123,
                    "restingHeartRate": 52,
                    "activeKilocalories": 540,
                },
                "get_sleep_data": {
                    "dailySleepDTO": {
                        "calendarDate": "2026-09-21",
                        "sleepTimeSeconds": 27000,
                        "deepSleepSeconds": 5400,
                        "sleepScores": {"overall": {"value": 81}},
                    }
                },
                "get_hrv_data": {"hrvSummary": {"lastNightAvg": 48, "status": "BALANCED"}},
                "get_training_readiness": [{"score": 74}],
                "get_daily_weigh_ins": {"dateWeightList": [{"weight": 81400.0}]},
            }
        )
        summary = health.day(date(2026, 9, 21))

        assert summary.steps == 8123
        assert summary.resting_hr == 52
        assert summary.sleep is not None
        assert summary.sleep.sleep_score == 81
        assert summary.hrv_last_night_avg == 48
        assert summary.hrv_status == "BALANCED"
        assert summary.training_readiness == 74
        assert summary.weight is not None
        assert summary.weight.weight_kg == 81.4

    def test_a_day_with_nothing_recorded_is_not_an_error(self, fake_garmin: dict[str, Any]) -> None:
        """A watch left on the nightstand gives nulls, not a failure."""
        for name in (
            "get_stats",
            "get_sleep_data",
            "get_hrv_data",
            "get_training_readiness",
            "get_daily_weigh_ins",
        ):
            fake_garmin["responses"][name] = None

        summary = health.day(date(2026, 9, 21))
        assert summary.day == date(2026, 9, 21)
        assert summary.steps is None
        assert summary.sleep is None

    def test_one_failing_endpoint_does_not_sink_the_day(self, fake_garmin: dict[str, Any]) -> None:
        def explode(*_: Any) -> Any:
            raise RuntimeError("Garmin had an opinion")

        fake_garmin["responses"].update(
            {
                "get_stats": {"totalSteps": 100},
                "get_sleep_data": explode,
                "get_hrv_data": None,
                "get_training_readiness": None,
                "get_daily_weigh_ins": None,
            }
        )
        summary = health.day(date(2026, 9, 21))
        assert summary.steps == 100
        assert summary.sleep is None


class TestWorkouts:
    def test_a_strength_workout_becomes_garmins_dto(self) -> None:
        from garmin_api.models.workouts import StrengthExerciseSpec, StrengthWorkoutSpec

        dto = workouts.build_strength(
            StrengthWorkoutSpec(
                name="Push A",
                exercises=[
                    StrengthExerciseSpec(
                        category="BENCH_PRESS",
                        exercise_name="BARBELL_BENCH_PRESS",
                        sets=3,
                        reps=8,
                        weight_kg=60,
                        rest_s=120,
                    )
                ],
            )
        )

        assert dto["sportType"]["sportTypeKey"] == "strength_training"
        group = dto["workoutSegments"][0]["workoutSteps"][0]
        assert group["numberOfIterations"] == 3

        step = group["workoutSteps"][0]
        assert step["category"] == "BENCH_PRESS"
        assert step["exerciseName"] == "BARBELL_BENCH_PRESS"
        assert step["endConditionValue"] == 8.0
        assert step["weightValue"] == 60000.0, "Garmin wants grams here"

    def test_step_orders_stay_unique_across_exercises(self) -> None:
        from garmin_api.models.workouts import StrengthExerciseSpec, StrengthWorkoutSpec

        dto = workouts.build_strength(
            StrengthWorkoutSpec(
                name="Full",
                exercises=[
                    StrengthExerciseSpec(
                        category="BENCH_PRESS", exercise_name="BARBELL_BENCH_PRESS"
                    ),
                    StrengthExerciseSpec(category="ROW", exercise_name="BARBELL_ROW"),
                    StrengthExerciseSpec(category="SQUAT", exercise_name="BARBELL_BACK_SQUAT"),
                ],
            )
        )
        orders = []
        for group in dto["workoutSegments"][0]["workoutSteps"]:
            orders.append(group["stepOrder"])
            orders.extend(step["stepOrder"] for step in group["workoutSteps"])
        assert len(orders) == len(set(orders))

    def test_an_unknown_exercise_is_refused_before_garmin_sees_it(self) -> None:
        from garmin_api.models.workouts import StrengthExerciseSpec, StrengthWorkoutSpec

        with pytest.raises(InvalidRequest):
            workouts.build_strength(
                StrengthWorkoutSpec(
                    name="Nope",
                    exercises=[StrengthExerciseSpec(category="BENCH_PRESS", exercise_name="WRONG")],
                )
            )


class TestUploads:
    def test_a_successful_upload_reports_the_new_activity(
        self, fake_garmin, sample_session
    ) -> None:
        fake_garmin["responses"]["upload_activity"] = {
            "detailedImportResult": {"successes": [{"internalId": 555}], "failures": []}
        }
        fake_garmin["responses"]["set_activity_name"] = {}

        result = uploads.log_strength_session(sample_session)
        assert result["status"] == "created"
        assert result["activity_id"] == 555
        assert result["sets_uploaded"] == 4

    def test_a_repeat_upload_is_reported_as_a_duplicate(self, fake_garmin, sample_session) -> None:
        """Re-running a sync must not create a second copy of a workout."""
        fake_garmin["responses"]["upload_activity"] = {
            "detailedImportResult": {
                "successes": [],
                "failures": [
                    {
                        "internalId": 555,
                        "messages": [{"content": "Duplicate activity"}],
                    }
                ],
            }
        }
        result = uploads.log_strength_session(sample_session, rename=False)
        assert result["status"] == "duplicate"
        assert result["activity_id"] == 555

    def test_a_failed_rename_does_not_lose_the_upload(self, fake_garmin, sample_session) -> None:
        def explode(*_: Any) -> Any:
            raise RuntimeError("rename refused")

        fake_garmin["responses"]["upload_activity"] = {
            "detailedImportResult": {"successes": [{"internalId": 777}], "failures": []}
        }
        fake_garmin["responses"]["set_activity_name"] = explode

        result = uploads.log_strength_session(sample_session)
        assert result["status"] == "created"
        assert result["activity_id"] == 777
        assert "rename_failed" in result

    def test_an_unknown_exercise_never_reaches_garmin(self, fake_garmin) -> None:
        from garmin_api.models.workouts import (
            StrengthSessionExercise,
            StrengthSessionSet,
            StrengthSessionSpec,
        )

        spec = StrengthSessionSpec(
            name="t",
            start_time=datetime(2026, 9, 21, tzinfo=UTC),
            exercises=[
                StrengthSessionExercise(
                    category="BENCH_PRESS",
                    exercise_name="NOT_REAL",
                    sets=[StrengthSessionSet(reps=1)],
                )
            ],
        )
        with pytest.raises(InvalidRequest):
            uploads.log_strength_session(spec)
        assert fake_garmin["calls"] == []

    def test_an_unsupported_file_type_is_refused(self, tmp_path) -> None:
        bad = tmp_path / "workout.csv"
        bad.write_text("nope")
        with pytest.raises(InvalidRequest, match="Cannot upload") as raised:
            uploads.upload_file(bad)
        assert "Garmin accepts" in (raised.value.detail or "")


class TestWeight:
    def test_weigh_ins_convert_grams(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_body_composition"] = {
            "dateWeightList": [{"weight": 81400.0, "bodyFat": 18.2, "calendarDate": "2026-09-21"}]
        }
        found = weight.weigh_ins(date(2026, 9, 1), date(2026, 9, 30))
        assert found[0].weight_kg == 81.4
        assert found[0].body_fat_pct == 18.2

    def test_adding_a_weight_sends_kilograms(self, fake_garmin: dict[str, Any]) -> None:
        """This endpoint is the exception: it takes kg, not grams."""
        fake_garmin["responses"]["add_weigh_in"] = {}
        weight.add_weigh_in(81.4)
        _, args = fake_garmin["calls"][0]
        assert args[0] == 81.4
        assert args[1] == "kg"

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


class TestHealthDoesNotHideAccountProblems:
    """Tolerating a missing metric must not mean tolerating a locked account."""

    @pytest.mark.parametrize("error", ["rate", "auth"])
    def test_account_errors_propagate(self, fake_garmin: dict[str, Any], error: str) -> None:
        from garmin_api.errors import NotAuthenticated, RateLimited

        exc = RateLimited("slow down") if error == "rate" else NotAuthenticated("log in")

        def explode(*_: Any) -> Any:
            raise exc

        fake_garmin["responses"]["get_stats"] = explode
        with pytest.raises(type(exc)):
            health.day(date(2026, 9, 21))

        assert len(fake_garmin["calls"]) == 1, "no further calls after the account fails"


# A slice of Garmin's real activity type tree: "all" is the root, its children
# are top-level, and anything deeper is a sub-type.
TYPES = [
    {"typeId": 17, "typeKey": "all", "parentTypeId": None},
    {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
    {"typeId": 26, "typeKey": "swimming", "parentTypeId": 17},
    {"typeId": 27, "typeKey": "lap_swimming", "parentTypeId": 26},
    {"typeId": 29, "typeKey": "fitness_equipment", "parentTypeId": 17},
    {"typeId": 13, "typeKey": "strength_training", "parentTypeId": 29},
]


class TestActivityTypeFilter:
    @pytest.fixture(autouse=True)
    def types(self, fake_garmin: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(activities, "_types_cache", None)
        fake_garmin["responses"]["get_activity_types"] = TYPES

    def test_a_top_level_type_is_sent_as_is(self) -> None:
        assert activities.resolve_type("running") == ("running", None)

    def test_strength_is_a_sub_type_of_fitness_equipment(self) -> None:
        """Sending strength_training as a type earns a 400 from Garmin."""
        assert activities.resolve_type("strength_training") == (
            "fitness_equipment",
            "strength_training",
        )

    def test_list_sends_the_sub_type_separately(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activities"] = []
        activities.list_activities(activity_type="lap_swimming")
        name, args = fake_garmin["calls"][-1]
        assert name == "get_activities"
        assert args[2:] == ("swimming", "lap_swimming")

    def test_a_date_range_narrows_the_sub_type_itself(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activities_by_date"] = [
            {"activityId": 1, "activityType": {"typeKey": "lap_swimming"}},
            {"activityId": 2, "activityType": {"typeKey": "open_water_swimming"}},
        ]
        found = activities.list_activities(
            activity_type="lap_swimming", start_date=date(2026, 8, 1)
        )
        assert [a.activity_id for a in found] == [1]


class TestRealDataMappings:
    """Mistakes that only showed up against a real account."""

    def test_a_swim_is_not_reported_as_having_sets(self, fake_garmin: dict[str, Any]) -> None:
        swim = {
            "activityId": 1,
            "activityType": {"typeKey": "lap_swimming"},
            "strokes": 900,
            "hasSplits": True,
        }
        fake_garmin["responses"]["get_activities"] = [swim, ACTIVITY_ROW]
        found = activities.list_activities()
        assert [a.has_sets for a in found] == [False, True]

    def test_local_start_time_carries_no_false_timezone(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_activities"] = [ACTIVITY_ROW]
        only = activities.list_activities()[0]
        assert only.start_time_local == "2026-09-21T07:00:00"
        assert only.start_time is not None and only.start_time.tzinfo is not None

    def test_vigorous_minutes_are_kept(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"].update(
            {
                "get_stats": {"moderateIntensityMinutes": 20, "vigorousIntensityMinutes": 15},
                "get_sleep_data": None,
                "get_hrv_data": None,
                "get_training_readiness": None,
                "get_daily_weigh_ins": None,
            }
        )
        summary = health.day(date(2026, 9, 21))
        assert summary.moderate_intensity_minutes == 20
        assert summary.vigorous_intensity_minutes == 15


class TestCalendar:
    """Shapes captured from a real account on 23 Sep 2026."""

    CALENDAR = {
        "month": 8,
        "year": 2026,
        "calendarItems": [
            {"itemType": "nap", "id": None, "date": "2026-08-21"},
            {"itemType": "activity", "id": 18800000002, "title": "Pool Swim", "date": "2026-08-20"},
            {
                "id": 1600000001,
                "itemType": "workout",
                "title": "Push A",
                "date": "2026-09-28",
                "sportTypeKey": "strength_training",
                "workoutId": 1600000002,
            },
            # The grid spills into the next month's first days.
            {
                "id": 1,
                "itemType": "workout",
                "title": "Spill",
                "date": "2026-10-01",
                "workoutId": 2,
            },
        ],
    }

    def test_only_workouts_in_the_month_are_returned(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["get_scheduled_workouts"] = self.CALENDAR
        found = workouts.scheduled(2026, 9)

        assert len(found) == 1
        entry = found[0]
        assert entry.schedule_id == 1600000001
        assert entry.workout_id == 1600000002
        assert entry.name == "Push A"
        assert entry.sport == "strength_training"
        assert entry.day == date(2026, 9, 28)

    def test_scheduling_returns_the_schedule_id(self, fake_garmin: dict[str, Any]) -> None:
        """unschedule takes this id, so the caller must be given it."""
        fake_garmin["responses"]["schedule_workout"] = {
            "workoutScheduleId": 1600000001,
            "workout": {
                "workoutId": 1600000002,
                "workoutName": "Push A",
                "sportType": {"sportTypeKey": "strength_training"},
            },
        }
        entry = workouts.schedule(1600000002, date(2026, 9, 28))
        assert entry.schedule_id == 1600000001
        assert entry.workout_id == 1600000002
        assert entry.day == date(2026, 9, 28)


def test_an_upload_with_no_activity_id_is_processed_not_created(
    fake_garmin: dict[str, Any],
) -> None:
    """Weight files: Garmin says "File processed." and gives no id, ever."""
    from garmin_api.services.uploads import _interpret_upload

    result = _interpret_upload(
        {
            "detailedImportResult": {
                "successes": [
                    {"internalId": None, "messages": [{"code": 200, "content": "File processed."}]}
                ],
                "failures": [],
            }
        }
    )
    assert result["status"] == "processed"
    assert result["activity_id"] is None

"""Planned workouts: create, schedule, push to a watch.

A workout here is a plan, not a record of something done. For logging a
finished session see :mod:`garmin_api.services.uploads`.

The DTO Garmin's workout service wants is deep and fussy, so the typed
builders python-garminconnect ships are reused rather than reinvented. What
this layer adds is validation: every exercise is checked against the catalogue
first, so a typo comes back as a useful message instead of Garmin's bare 400.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from garminconnect.workout import (
    ExecutableStep,
    RepeatGroup,
    StrengthWorkout,
    WorkoutSegment,
    create_strength_set,
)

from .. import auth, catalogue
from ..errors import GarminApiError, InvalidRequest, NotFound
from ..models.common import as_date_str
from ..models.health import DeviceSummary
from ..models.workouts import (
    EnduranceWorkoutSpec,
    ScheduledWorkout,
    StrengthWorkoutSpec,
    WorkoutSummary,
)

SPORT_STRENGTH = {
    "sportTypeId": 5,
    "sportTypeKey": "strength_training",
    "displayOrder": 5,
}


def list_workouts(
    *, start: int = 0, limit: int = 100, include_raw: bool = False
) -> list[WorkoutSummary]:
    rows = auth.call("get_workouts", start, limit) or []
    return [WorkoutSummary.from_garmin(r, include_raw=include_raw) for r in rows]


def get_workout(workout_id: int | str, *, include_raw: bool = True) -> WorkoutSummary:
    payload = auth.call("get_workout_by_id", workout_id)
    if not payload:
        raise NotFound(f"No workout {workout_id}.")
    return WorkoutSummary.from_garmin(payload, include_raw=include_raw)


def build_strength(spec: StrengthWorkoutSpec) -> dict[str, Any]:
    """Turn a compact spec into Garmin's workout DTO, validating as it goes.

    Each exercise becomes one repeat group of "do the reps, then rest", which
    is the shape the Connect editor produces and the watch knows how to follow.
    Step orders must be unique across the whole segment, hence the counter.
    """
    steps: list[ExecutableStep | RepeatGroup] = []
    order = 1
    estimated = 0
    for exercise in spec.exercises:
        catalogue.validate(exercise.category, exercise.exercise_name)
        steps.append(
            create_strength_set(
                category=exercise.category,
                step_order=order,
                sets=exercise.sets,
                reps=exercise.reps,
                rest_seconds=exercise.rest_s,
                exercise_name=exercise.exercise_name,
                weight_kg=exercise.weight_kg,
            )
        )
        # Three orders per block: the group, its exercise step, its rest step.
        order += 3
        # Rough but honest: about four seconds a rep, plus the rests.
        estimated += exercise.sets * (exercise.reps * 4 + exercise.rest_s)

    workout = StrengthWorkout(
        workoutName=spec.name,
        description=spec.description,
        estimatedDurationInSecs=int(estimated),
        workoutSegments=[
            WorkoutSegment(segmentOrder=1, sportType=SPORT_STRENGTH, workoutSteps=steps)
        ],
    )
    return workout.to_dict()


def create_strength(spec: StrengthWorkoutSpec) -> WorkoutSummary:
    """Create a strength workout, and schedule it if the spec says to."""
    payload = build_strength(spec)
    created = auth.call("upload_workout", payload)
    summary = WorkoutSummary.from_garmin(created or {}, include_raw=True)
    _schedule_new(summary, spec.schedule_date)
    return summary


def _schedule_new(summary: WorkoutSummary, day: date | None) -> None:
    """Schedule a workout just created, keeping its id if that fails.

    The workout exists by now, so the caller has to learn its id: retrying
    the whole request would create a second copy.
    """
    if not day or not summary.workout_id:
        return
    try:
        schedule(summary.workout_id, day)
    except GarminApiError as exc:
        exc.detail = (
            f"The workout was created as {summary.workout_id} but not scheduled; "
            f"schedule that one rather than creating it again. {exc.detail or ''}"
        ).strip()
        raise


def create_raw(workout_json: dict[str, Any]) -> WorkoutSummary:
    """Create a workout from a full Garmin DTO, for shapes the builder omits."""
    created = auth.call("upload_workout", workout_json)
    return WorkoutSummary.from_garmin(created or {}, include_raw=True)


def update(workout_id: int | str, workout_json: dict[str, Any]) -> Any:
    return auth.call("update_workout", workout_id, workout_json)


def delete(workout_id: int | str) -> Any:
    return auth.call("delete_workout", workout_id)


def schedule(workout_id: int | str, day: date | str) -> ScheduledWorkout:
    """Put a workout on the calendar for a date.

    Returns the calendar entry, whose ``schedule_id`` is what
    :func:`unschedule` takes -- a different number from the workout id.
    """
    day_str = as_date_str(day)
    payload = auth.call("schedule_workout", workout_id, day_str) or {}
    entry = ScheduledWorkout.from_garmin(payload)
    if entry.day is None:
        entry.day = date.fromisoformat(day_str)
    if entry.workout_id is None:
        entry.workout_id = int(workout_id)
    return entry


def unschedule(schedule_id: int | str) -> Any:
    """Remove a calendar entry. Takes the schedule id, not the workout id."""
    return auth.call("unschedule_workout", schedule_id)


def scheduled(year: int, month: int, *, include_raw: bool = False) -> list[ScheduledWorkout]:
    """Workouts scheduled in a month. ``month`` is 1-12.

    Garmin's endpoint is really the whole calendar: completed activities,
    naps and badges sit beside the scheduled workouts, and the grid spills
    into the days either side of the month. Both are filtered out here, so
    this answers only the question its name asks.
    """
    payload = auth.call("get_scheduled_workouts", year, month) or {}
    rows = payload.get("calendarItems") or []
    entries = [
        ScheduledWorkout.from_garmin(r, include_raw=include_raw)
        for r in rows
        if r.get("itemType") == "workout"
    ]
    return [e for e in entries if e.day is None or (e.day.year == year and e.day.month == month)]


def push_to_device(workout_id: int | str, device_id: int | str | None = None) -> DeviceSummary:
    """Send a workout to a watch, and say which one.

    The watch picks it up at its next sync, not instantly. A push only ever
    goes to a device on the account's device list. Garmin's "last used"
    device is not always one: an uploaded file, a smart scale's weigh-in or a
    session logged by this package makes Garmin record a pseudo-device named
    "Default", and a workout pushed there never reaches a watch. So with no
    device id the last-used device is taken only if it is on the list, then
    the primary activity tracker, then the only device.
    """
    listed = devices()
    if device_id is not None:
        target = next((d for d in listed if d.device_id == int(device_id)), None)
        if target is None:
            raise InvalidRequest(
                f"Device {device_id} is not on this account.",
                detail=_device_choices(listed),
            )
    else:
        target = _default_push_target(listed)
    auth.call("push_workout_to_device", workout_id, target.device_id)
    return target


def _default_push_target(listed: list[DeviceSummary]) -> DeviceSummary:
    last = auth.call("get_device_last_used") or {}
    last_id = last.get("userDeviceId") if isinstance(last, dict) else None
    by_id = {d.device_id: d for d in listed}
    if last_id is not None and int(last_id) in by_id:
        return by_id[int(last_id)]
    primary = [d for d in listed if d.primary]
    if len(primary) == 1:
        return primary[0]
    if len(listed) == 1:
        return listed[0]
    raise InvalidRequest(
        "Not sure which device to push to." if listed else "No devices on this account.",
        detail=_device_choices(listed),
    )


def _device_choices(listed: list[DeviceSummary]) -> str:
    if not listed:
        return "Pair a watch with Garmin Connect first; list_devices shows what is paired."
    names = ", ".join(f"{d.name or d.product or 'unnamed'} ({d.device_id})" for d in listed)
    return f"Pass device_id, one of: {names}."


def devices() -> list[DeviceSummary]:
    """Devices on the account. Garmin's own entry is ~300 capability flags each."""
    return [DeviceSummary.from_garmin(d) for d in (auth.call("get_devices") or [])]


# --- Swim, run and ride ------------------------------------------------------

STEP_TYPES = {
    "warmup": (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "recovery": (4, "recovery"),
    "rest": (5, "rest"),
}
SPORTS = {
    "running": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
    "swimming": {"sportTypeId": 4, "sportTypeKey": "swimming", "displayOrder": 3},
}
# Used only for the estimated duration Garmin shows, when a step ends on a
# distance and sets no pace: a steady run, ride and swim.
DEFAULT_SPEED_MPS = {"running": 1000 / 360, "cycling": 25 / 3.6, "swimming": 100 / 120}


def _target_fields(target: Any) -> dict[str, Any]:
    """Garmin's target fields for one of the spec's targets."""
    from garminconnect.workout import (
        CadenceTarget,
        CustomHeartRateTarget,
        CustomPowerTarget,
        HeartRateZoneTarget,
        PaceTarget,
        PowerZoneTarget,
        SpeedTarget,
    )

    from ..models.workouts import _seconds

    if target.pace_per_km or target.pace_per_mile:
        slow, fast = target.pace_per_km or target.pace_per_mile
        metres = 1000.0 if target.pace_per_km else 1609.344
        # Garmin wants speeds, slower pace as the lower limit.
        built: Any = PaceTarget(
            lower_limit=metres / _seconds(slow), upper_limit=metres / _seconds(fast)
        )
    elif target.speed_kph:
        built = SpeedTarget(
            lower_limit=target.speed_kph[0] / 3.6, upper_limit=target.speed_kph[1] / 3.6
        )
    elif target.heart_rate_zone:
        built = HeartRateZoneTarget(zone_number=target.heart_rate_zone)
    elif target.heart_rate_bpm:
        built = CustomHeartRateTarget(
            lower_limit=target.heart_rate_bpm[0], upper_limit=target.heart_rate_bpm[1]
        )
    elif target.power_zone:
        built = PowerZoneTarget(zone_number=target.power_zone)
    elif target.power_watts:
        built = CustomPowerTarget(
            lower_limit=target.power_watts[0], upper_limit=target.power_watts[1]
        )
    else:
        built = CadenceTarget(lower_limit=target.cadence[0], upper_limit=target.cadence[1])
    return {
        "targetType": {
            "workoutTargetTypeId": built.target_type,
            "workoutTargetTypeKey": built.target_type_key,
            "displayOrder": 1,
        },
        "targetValueOne": built.lower_limit,
        "targetValueTwo": built.upper_limit,
        "zoneNumber": built.zone_number,
    }


def _endurance_step(step: Any, order: int, sport: str) -> tuple[Any, float]:
    """One executable step, and its estimated seconds."""
    from garminconnect.workout import ExecutableStep

    from ..models.workouts import EQUIPMENT, STROKES

    type_id, type_key = STEP_TYPES[step.kind]
    if step.lap_button:
        end = {
            "conditionTypeId": 1,
            "conditionTypeKey": "lap.button",
            "displayOrder": 1,
            "displayable": True,
        }
        value = None
    elif step.distance_m is not None:
        end = {
            "conditionTypeId": 3,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }
        value = round(step.distance_m, 2)
    elif step.kind == "rest" and sport == "swimming":
        # A swim rest is a fixed rest on the wall, which is how Garmin's
        # editor writes it and how the watch counts it down.
        end = {
            "conditionTypeId": 8,
            "conditionTypeKey": "fixed.rest",
            "displayOrder": 8,
            "displayable": True,
        }
        value = step.duration_s
    else:
        end = {
            "conditionTypeId": 2,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }
        value = step.duration_s

    fields: dict[str, Any] = {
        "stepOrder": order,
        "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": type_id},
        "endCondition": end,
        "endConditionValue": value,
        "targetType": {
            "workoutTargetTypeId": 1,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
    }
    if step.target is not None:
        fields |= _target_fields(step.target)
    if step.stroke:
        stroke_id, stroke_key = STROKES[step.stroke]
        fields["strokeType"] = {
            "strokeTypeId": stroke_id,
            "strokeTypeKey": stroke_key,
            "displayOrder": stroke_id,
        }
    if step.equipment:
        kit_id, kit_key = EQUIPMENT[step.equipment]
        fields["equipmentType"] = {
            "equipmentTypeId": kit_id,
            "equipmentTypeKey": kit_key,
            "displayOrder": kit_id,
        }
    if step.note:
        fields["description"] = step.note

    if step.duration_s is not None:
        seconds = step.duration_s
    elif step.distance_m is not None:
        speed = DEFAULT_SPEED_MPS[sport]
        # A pace or speed target is a speed in m/s; use its midpoint.
        if step.target is not None and (
            step.target.pace_per_km or step.target.pace_per_mile or step.target.speed_kph
        ):
            speed = (fields["targetValueOne"] + fields["targetValueTwo"]) / 2
        seconds = step.distance_m / speed
    else:
        seconds = 60.0  # lap button: unknown, so a nominal minute
    return ExecutableStep(**fields), seconds


def build_endurance(spec: EnduranceWorkoutSpec) -> dict[str, Any]:
    """Garmin's workout format for a run, ride or pool swim."""
    from garminconnect.workout import (
        CyclingWorkout,
        RunningWorkout,
        SwimmingWorkout,
        WorkoutSegment,
        create_repeat_group,
    )

    from ..models.workouts import EnduranceRepeat

    steps: list[Any] = []
    order = 1
    estimated = 0.0
    for item in spec.steps:
        if isinstance(item, EnduranceRepeat):
            group_order = order
            order += 1
            children = []
            per_round = 0.0
            for child in item.steps:
                built, seconds = _endurance_step(child, order, spec.sport)
                children.append(built)
                per_round += seconds
                order += 1
            steps.append(create_repeat_group(item.repeat, children, group_order))
            estimated += per_round * item.repeat
        else:
            built, seconds = _endurance_step(item, order, spec.sport)
            steps.append(built)
            estimated += seconds
            order += 1

    cls = {"running": RunningWorkout, "cycling": CyclingWorkout, "swimming": SwimmingWorkout}[
        spec.sport
    ]
    extra: dict[str, Any] = {}
    if spec.sport == "swimming" and spec.pool_length:
        extra["poolLength"] = spec.pool_length
        extra["poolLengthUnit"] = (
            {"unitId": 230, "unitKey": "yard", "factor": 91.44}
            if spec.pool_unit == "yd"
            else {"unitId": 1, "unitKey": "meter", "factor": 100.0}
        )
    workout = cls(
        workoutName=spec.name,
        description=spec.description,
        estimatedDurationInSecs=int(estimated),
        workoutSegments=[
            WorkoutSegment(segmentOrder=1, sportType=SPORTS[spec.sport], workoutSteps=steps)
        ],
        **extra,
    )
    return workout.to_dict()


def create_endurance(spec: EnduranceWorkoutSpec) -> WorkoutSummary:
    """Create a run, ride or pool swim workout, and schedule it if asked."""
    created = auth.call("upload_workout", build_endurance(spec))
    summary = WorkoutSummary.from_garmin(created or {}, include_raw=True)
    _schedule_new(summary, spec.schedule_date)
    return summary

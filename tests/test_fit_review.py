"""Regressions from the FIT codec review of 19 Oct 2026."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.set_message import (
    SetMessage,
    SetRepetitionsField,
    SetWeightField,
)
from fit_tool.profile.messages.weight_scale_message import (
    WeightScaleMessage,
    WeightScalePercentFatField,
)
from fit_tool.profile.profile_type import FileType, SetType
from pydantic import ValidationError

from garmin_api.fit import decode_strength_session, encode_strength_session
from garmin_api.fit.body import decode_body_composition
from garmin_api.models.workouts import (
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
)

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)


def _ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def _sets(data: bytes) -> list[SetMessage]:
    return [
        r.message for r in FitFile.from_bytes(data).records if isinstance(r.message, SetMessage)
    ]


def _session(*exercises: StrengthSessionExercise, start: datetime = T0) -> StrengthSessionSpec:
    return StrengthSessionSpec(name="t", start_time=start, exercises=list(exercises))


def _watch_file(*, empty_weight: bool = False, empty_reps: bool = False) -> bytes:
    """One pull-up set as a watch writes it, with fields present but holding no value."""
    builder = FitFileBuilder(auto_define=True)
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.serial_number = 1
    file_id.time_created = _ms(T0)
    builder.add(file_id)
    working = SetMessage()
    working.set_type = SetType.ACTIVE
    working.start_time = _ms(T0 + timedelta(minutes=2))
    working.timestamp = _ms(T0 + timedelta(minutes=2, seconds=30))
    working.duration = 30.0
    working.repetitions = 10
    working.weight = 20.0
    working.category = [21]
    if empty_weight:
        working.get_field(SetWeightField.ID).set_encoded_value(0, 0xFFFF)
    if empty_reps:
        working.get_field(SetRepetitionsField.ID).set_encoded_value(0, 0xFFFF)
    builder.add(working)
    session = SessionMessage()
    session.start_time = _ms(T0)
    session.timestamp = _ms(T0 + timedelta(minutes=3))
    builder.add(session)
    return bytes(builder.build().to_bytes())


def test_a_bodyweight_set_with_no_weight_decodes_as_none() -> None:
    entry = decode_strength_session(_watch_file(empty_weight=True)).exercises[0].sets[0]
    assert entry.weight_kg is None
    assert entry.reps == 10


def test_a_timed_set_with_no_reps_decodes_as_none() -> None:
    assert decode_strength_session(_watch_file(empty_reps=True)).exercises[0].sets[0].reps is None


def test_a_weigh_in_with_an_empty_field_decodes() -> None:
    builder = FitFileBuilder(auto_define=True)
    file_id = FileIdMessage()
    file_id.type = FileType.WEIGHT
    file_id.serial_number = 1
    file_id.time_created = _ms(T0)
    builder.add(file_id)
    scale = WeightScaleMessage()
    scale.timestamp = _ms(T0)
    scale.weight = 72.4
    scale.percent_fat = 18.0
    scale.get_field(WeightScalePercentFatField.ID).set_encoded_value(0, 0xFFFF)
    builder.add(scale)
    [weigh_in] = decode_body_composition(bytes(builder.build().to_bytes()))
    assert weigh_in.weight_kg == pytest.approx(72.4)
    assert weigh_in.percent_fat is None


def test_the_session_start_wins_over_the_first_set() -> None:
    """The session message comes after the sets, and the first set may start late."""
    assert decode_strength_session(_watch_file()).start_time == T0


def test_no_rest_between_sets_survives_a_round_trip() -> None:
    spec = _session(
        StrengthSessionExercise(
            category="BENCH_PRESS",
            exercise_name="BARBELL_BENCH_PRESS",
            sets=[
                StrengthSessionSet(reps=5, duration_s=30, rest_s=0),
                StrengthSessionSet(reps=5, duration_s=30, rest_s=90),
                StrengthSessionSet(reps=5, duration_s=30),
            ],
        )
    )
    decoded = decode_strength_session(encode_strength_session(spec))
    assert [s.rest_s for s in decoded.exercises[0].sets] == [0.0, 90.0, None]
    assert encode_strength_session(decoded) == encode_strength_session(spec)


def test_a_generic_exercise_can_be_given_by_category_alone() -> None:
    exercise = StrengthSessionExercise(category="bench_press", sets=[StrengthSessionSet(reps=5)])
    assert exercise.exercise_name == "BENCH_PRESS"


def test_a_timed_set_carries_no_reps() -> None:
    spec = _session(
        StrengthSessionExercise(category="PLANK", sets=[StrengthSessionSet(duration_s=60)])
    )
    [plank] = [
        s for s in _sets(encode_strength_session(spec)) if s.set_type == SetType.ACTIVE.value
    ]
    assert plank.repetitions is None


def test_a_reused_exercise_object_still_gets_its_rests() -> None:
    """An alternating superset built in Python repeats the same object."""
    bench = StrengthSessionExercise(
        category="BENCH_PRESS",
        exercise_name="BARBELL_BENCH_PRESS",
        sets=[StrengthSessionSet(reps=5)],
    )
    row = StrengthSessionExercise(
        category="ROW", exercise_name="BARBELL_ROW", sets=[StrengthSessionSet(reps=5)]
    )
    kinds = [s.set_type for s in _sets(encode_strength_session(_session(bench, row, bench)))]
    active, rest = SetType.ACTIVE.value, SetType.REST.value
    assert kinds == [active, rest, active, rest, active]


def test_a_rest_across_a_daylight_saving_change_lasts_as_long_as_it_says() -> None:
    """New York falls back at 02:00 on 1 Nov 2026; a 600 s rest spans it."""
    start = datetime(2026, 11, 1, 1, 50, tzinfo=ZoneInfo("America/New_York"))
    spec = _session(
        StrengthSessionExercise(
            category="BENCH_PRESS",
            exercise_name="BARBELL_BENCH_PRESS",
            sets=[
                StrengthSessionSet(reps=10, duration_s=30, rest_s=600),
                StrengthSessionSet(reps=10, duration_s=30),
            ],
        ),
        start=start,
    )
    records = [r.message for r in FitFile.from_bytes(encode_strength_session(spec)).records]
    session = next(m for m in records if isinstance(m, SessionMessage))
    assert (session.timestamp - session.start_time) / 1000 == pytest.approx(660)
    assert session.start_time == _ms(start)


def test_an_impossible_duration_is_refused_before_encoding() -> None:
    with pytest.raises(ValidationError):
        StrengthSessionSet(reps=1, duration_s=5_000_000)
    with pytest.raises(ValidationError):
        StrengthSessionSet(reps=1, rest_s=5_000_000)

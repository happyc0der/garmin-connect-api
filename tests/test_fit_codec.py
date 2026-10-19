"""The FIT codec: the write path Garmin's own API never offered.

The round trip is the contract that matters. If a session survives encode and
decode unchanged, the file carries everything Garmin needs to show it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from garmin_api.errors import InvalidRequest
from garmin_api.fit import decode_strength_session, encode_strength_session
from garmin_api.fit import exercises as fx
from garmin_api.models.workouts import (
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
)


def test_encodes_a_real_fit_file(sample_session: StrengthSessionSpec) -> None:
    data = encode_strength_session(sample_session)
    assert data[8:12] == b".FIT"
    assert len(data) > 200


def test_round_trip_preserves_every_set(sample_session: StrengthSessionSpec) -> None:
    decoded = decode_strength_session(encode_strength_session(sample_session))

    assert decoded.total_sets == sample_session.total_sets
    assert len(decoded.exercises) == len(sample_session.exercises)

    for original, returned in zip(sample_session.exercises, decoded.exercises, strict=True):
        assert returned.category == original.category
        assert returned.exercise_name == original.exercise_name
        for before, after in zip(original.sets, returned.sets, strict=True):
            assert after.reps == before.reps
            assert after.weight_kg == pytest.approx(before.weight_kg, abs=0.07)
            assert after.rest_s == pytest.approx(before.rest_s or 0, abs=1)


def test_weight_survives_the_sixteenth_of_a_kilo_grid() -> None:
    """FIT stores weight scaled by 16, so 2.5kg plates land exactly."""
    spec = StrengthSessionSpec(
        name="t",
        start_time=datetime(2026, 9, 21, 7, 0, tzinfo=UTC),
        exercises=[
            StrengthSessionExercise(
                category="BENCH_PRESS",
                exercise_name="BARBELL_BENCH_PRESS",
                sets=[StrengthSessionSet(reps=5, weight_kg=w) for w in (2.5, 42.5, 100.0, 127.5)],
            )
        ],
    )
    decoded = decode_strength_session(encode_strength_session(spec))
    assert [s.weight_kg for s in decoded.exercises[0].sets] == [2.5, 42.5, 100.0, 127.5]


def test_timezone_is_preserved_as_an_instant() -> None:
    """A 07:00 session in UTC+2 is 05:00 UTC, and must not drift."""
    local = timezone(timedelta(hours=2))
    spec = StrengthSessionSpec(
        name="t",
        start_time=datetime(2026, 9, 21, 7, 0, tzinfo=local),
        exercises=[
            StrengthSessionExercise(
                category="BENCH_PRESS",
                exercise_name="BARBELL_BENCH_PRESS",
                sets=[StrengthSessionSet(reps=5, weight_kg=60.0)],
            )
        ],
    )
    decoded = decode_strength_session(encode_strength_session(spec))
    assert decoded.start_time == datetime(2026, 9, 21, 5, 0, tzinfo=UTC)


def test_generic_exercise_without_a_variant_round_trips() -> None:
    """Garmin repeats the category as the exercise for the generic entry."""
    spec = StrengthSessionSpec(
        name="t",
        start_time=datetime(2026, 9, 21, 7, 0, tzinfo=UTC),
        exercises=[
            StrengthSessionExercise(
                category="BENCH_PRESS",
                exercise_name="BENCH_PRESS",
                sets=[StrengthSessionSet(reps=5, weight_kg=60.0)],
            )
        ],
    )
    decoded = decode_strength_session(encode_strength_session(spec))
    assert decoded.exercises[0].category == "BENCH_PRESS"
    assert decoded.exercises[0].exercise_name == ""


def test_same_serial_encodes_identically(sample_session: StrengthSessionSpec) -> None:
    """Determinism is what makes an upload idempotent on Garmin's side."""
    first = encode_strength_session(sample_session, serial=4242)
    second = encode_strength_session(sample_session, serial=4242)
    assert first == second


def test_decoding_a_file_with_no_sets_says_so(non_strength_fit: bytes) -> None:
    with pytest.raises(InvalidRequest, match="No strength sets"):
        decode_strength_session(non_strength_fit)


class TestExerciseMapping:
    """Garmin's names against the FIT profile's, across the whole catalogue."""

    def test_every_catalogue_entry_maps_to_the_fit_profile(self) -> None:
        from garmin_api import catalogue

        unmapped = []
        for exercise in catalogue.all_exercises():
            try:
                fx.category_value(exercise.category)
                fx.subtype_value(exercise.category, exercise.exercise)
            except InvalidRequest:
                unmapped.append((exercise.category, exercise.exercise))
        assert unmapped == []

    def test_digit_prefixed_names_are_translated(self) -> None:
        """Garmin writes _45_DEGREE_PLANK; the FIT profile writes N45_....."""
        value = fx.subtype_value("PLANK", "_45_DEGREE_PLANK")
        assert value is not None
        assert fx.exercise_name(fx.category_value("PLANK"), value) == "_45_DEGREE_PLANK"

    def test_generic_entry_has_no_subtype(self) -> None:
        assert fx.subtype_value("BENCH_PRESS", "BENCH_PRESS") is None
        assert fx.subtype_value("BENCH_PRESS", "") is None


def test_the_same_session_encodes_identically_by_default(
    sample_session: StrengthSessionSpec,
) -> None:
    """No serial given: the same session must still yield the same file.

    Otherwise every run of a sync looks like a new activity to Garmin.
    """
    assert encode_strength_session(sample_session) == encode_strength_session(sample_session)


def test_different_sessions_get_different_serials(sample_session: StrengthSessionSpec) -> None:
    from garmin_api.fit.encode import session_serial

    changed = sample_session.model_copy(update={"name": "Push B"})
    assert session_serial(changed) != session_serial(sample_session)
    assert 0 < session_serial(sample_session) < 2**31


def test_the_file_carries_records_that_date_it(sample_session: StrengthSessionSpec) -> None:
    """The FIT spec requires records in an activity; without them Strava used the upload time."""
    from fit_tool.fit_file import FitFile
    from fit_tool.profile.messages.record_message import RecordMessage

    fit = FitFile.from_bytes(encode_strength_session(sample_session))
    stamps = [r.message.timestamp for r in fit.records if isinstance(r.message, RecordMessage)]
    assert stamps, "at least one record"
    assert stamps[0] == round(sample_session.start_time.timestamp() * 1000), (
        "first record is the start"
    )
    assert stamps == sorted(stamps)
    # Sets: 4 working + 4 rests (the last has a rest of 90s too) + one closing record.
    assert (
        len(stamps)
        == sum(
            1 + (1 if (s.rest_s or 0) > 0 else 0) for e in sample_session.exercises for s in e.sets
        )
        + 1
    )

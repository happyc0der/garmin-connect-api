"""The exercise catalogue, which every write validates against."""

from __future__ import annotations

import pytest

from garmin_api import catalogue
from garmin_api.errors import InvalidRequest


def test_catalogue_is_fully_loaded() -> None:
    assert len(catalogue.all_exercises()) > 1500
    assert len(catalogue.categories()) == 47
    assert "BENCH_PRESS" in catalogue.categories()


def test_resolve_is_case_and_punctuation_insensitive() -> None:
    expected = catalogue.resolve("Barbell Bench Press")
    assert expected is not None
    assert expected.category == "BENCH_PRESS"
    assert expected.exercise == "BARBELL_BENCH_PRESS"
    assert catalogue.resolve("barbell bench press") == expected
    assert catalogue.resolve("Barbell  Bench-Press") == expected


def test_validate_accepts_a_real_pair() -> None:
    found = catalogue.validate("bench_press", "barbell_bench_press")
    assert found.name == "Barbell Bench Press"


def test_validate_rejects_an_unknown_category_with_suggestions() -> None:
    with pytest.raises(InvalidRequest) as raised:
        catalogue.validate("BENCH_PRES", "BARBELL_BENCH_PRESS")
    assert "BENCH_PRESS" in (raised.value.detail or "")


def test_validate_rejects_an_unknown_exercise_in_a_real_category() -> None:
    with pytest.raises(InvalidRequest) as raised:
        catalogue.validate("BENCH_PRESS", "NOT_A_THING")
    assert "BENCH_PRESS" in raised.value.message


def test_match_refuses_to_guess_at_nonsense() -> None:
    """A wrong guess would silently log the wrong lift, so None is the answer."""
    assert catalogue.match("qwerty nonsense movement") is None


def test_match_handles_a_near_miss() -> None:
    found = catalogue.match("barbell bench pres")
    assert found is not None
    assert found.exercise == "BARBELL_BENCH_PRESS"

"""Mapping Garmin Connect's exercise names onto the FIT profile.

Both sides describe the same taxonomy, but spell it differently in two ways,
each verified against the whole 1,527-entry catalogue:

* Garmin's generic entry for a category repeats the category as the exercise
  ("BENCH_PRESS" / "BENCH_PRESS"). FIT has no such member; the file simply
  carries the category with no subtype, which is what "Bench Press" with no
  variant means.
* Names beginning with a digit are legal in neither language's identifiers.
  Garmin prefixes an underscore ("_45_DEGREE_PLANK"); fit-tool prefixes an N
  ("N45_DEGREE_PLANK").
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import fit_tool.profile.profile_type as profile
from fit_tool.profile.profile_type import ExerciseCategory

from ..errors import InvalidRequest


@lru_cache(maxsize=256)
def _name_enum(category: str) -> Any | None:
    """The per-category exercise-name enum, e.g. ``BenchPressExerciseName``."""
    camel = "".join(part.capitalize() for part in category.split("_"))
    return getattr(profile, camel + "ExerciseName", None)


def category_value(category: str) -> int:
    """The FIT ``exercise_category`` value for a Garmin category."""
    cat = category.upper().strip()
    member = getattr(ExerciseCategory, cat, None)
    if member is None:
        raise InvalidRequest(f"No FIT exercise category for {category!r}.")
    return int(member.value)


def subtype_value(category: str, exercise_name: str) -> int | None:
    """The FIT ``category_subtype`` value, or None for a generic exercise.

    None is a real answer, not a failure: it means "this category, no
    particular variant", which Garmin displays as the category's own name.
    """
    cat = category.upper().strip()
    name = (exercise_name or "").upper().strip()
    if not name or name == cat:
        return None

    enum = _name_enum(cat)
    if enum is None:
        raise InvalidRequest(f"No FIT exercise names for category {category!r}.")

    member = getattr(enum, name, None)
    if member is None and name.startswith("_"):
        member = getattr(enum, "N" + name[1:], None)
    if member is None:
        raise InvalidRequest(
            f"No FIT exercise name {exercise_name!r} in category {cat}.",
        )
    return int(member.value)


@lru_cache(maxsize=256)
def _reverse_category() -> dict[int, str]:
    return {int(m.value): m.name for m in ExerciseCategory}


def category_name(value: int) -> str | None:
    """Garmin's category string for a FIT category value."""
    return _reverse_category().get(int(value))


def exercise_name(category_value_: int, subtype: int | None) -> str | None:
    """Garmin's exercise string for a FIT category/subtype pair.

    Undoes the digit-prefix rename so a decoded file speaks the same names the
    rest of this package uses.
    """
    if subtype is None:
        return None
    cat = category_name(category_value_)
    if cat is None:
        return None
    enum = _name_enum(cat)
    if enum is None:
        return None
    for member in enum:
        if int(member.value) == int(subtype):
            name = member.name
            return "_" + name[1:] if name.startswith("N") and name[1:2].isdigit() else name
    return None

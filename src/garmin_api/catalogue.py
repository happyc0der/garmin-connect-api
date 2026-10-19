"""Garmin's strength exercise taxonomy.

Every strength exercise Garmin knows is a ``(category, exercise_name)`` pair,
such as ``("BENCH_PRESS", "BARBELL_BENCH_PRESS")``. The same pairs appear in
workout steps, in the ``exerciseSets`` of a completed activity and in FIT set
messages, and Garmin rejects anything outside the list with an unhelpful 400.
So every write validates against this first, and says what it would accept.

python-garminconnect bundles the catalogue (1,527 exercises, 47 categories)
scraped from the Connect workout editor, which is the same data served
publicly at connect.garmin.com/web-data/exercises/Exercises.json. Using the
bundled copy keeps the package working offline.
"""

from __future__ import annotations

import difflib
from functools import lru_cache
from typing import NamedTuple

from garminconnect import exercises as _catalogue

from .errors import InvalidRequest


class Exercise(NamedTuple):
    """One catalogue entry."""

    name: str
    """Display name, as Garmin Connect shows it, e.g. "Barbell Bench Press"."""
    category: str
    """Category enum, e.g. ``BENCH_PRESS``."""
    exercise: str
    """Variant enum, e.g. ``BARBELL_BENCH_PRESS``. May be empty."""


@lru_cache(maxsize=1)
def all_exercises() -> tuple[Exercise, ...]:
    return tuple(
        Exercise(name=e["name"], category=e["category"], exercise=e.get("exercise", ""))
        for e in _catalogue.EXERCISES
    )


@lru_cache(maxsize=1)
def categories() -> tuple[str, ...]:
    return tuple(sorted({e.category for e in all_exercises()}))


@lru_cache(maxsize=1)
def _by_pair() -> dict[tuple[str, str], Exercise]:
    return {(e.category, e.exercise): e for e in all_exercises()}


@lru_cache(maxsize=1)
def _by_lower_name() -> dict[str, Exercise]:
    return {e.name.lower(): e for e in all_exercises()}


# Gym names Garmin's catalogue does not use, mapped to the words it does.
# Each target was checked to find the intended exercise.
ALIASES = {
    "rdl": "romanian deadlift",
    "ohp": "shoulder press",
    "overhead press": "shoulder press",
    "skull crusher": "lying triceps extension",
    "skullcrusher": "lying triceps extension",
    "chinup": "chin up",
}


def _words(value: str) -> list[str]:
    """Lowercase alphanumeric words: "Lat Pull-down" -> ["lat", "pull", "down"]."""
    import re

    return re.findall(r"[a-z0-9]+", value.lower())


def _starts(word: str, token: str) -> bool:
    """A query word starts a name word, tolerating a plural: "dips" finds "Dip"."""
    if word.startswith(token):
        return True
    return len(token) >= 3 and token.endswith("s") and word.startswith(token[:-1])


def search(term: str, limit: int = 20) -> list[Exercise]:
    """Exercises matching ``term``, best match first.

    A name matches when every word of the query starts one of its words, so
    "pull up" finds "Pull-up" and "lat pulldown" finds "Lat Pull-down", while
    "rdl" no longer matches inside "Hurdle". A query of five or more letters
    also matches with spaces and hyphens ignored, so "pullup" works too.
    Common gym names Garmin does not use, like "skull crusher", are aliased.
    """
    query = " ".join(_words(term))
    query = ALIASES.get(query, ALIASES.get(query.replace(" ", ""), query))
    tokens = _words(query)
    if not tokens:
        return []
    joined = "".join(tokens)

    scored: list[tuple[int, int, str, Exercise]] = []
    for exercise in all_exercises():
        words = _words(exercise.name)
        flat = "".join(words)
        if words == tokens:
            rank = 0
        elif all(any(_starts(w, tok) for w in words) for tok in tokens):
            rank = 1
        elif len(joined) >= 5 and joined in flat:
            rank = 2
        else:
            continue
        scored.append((rank, len(words), exercise.name, exercise))
    scored.sort(key=lambda row: row[:3])
    return [row[3] for row in scored[:limit]]


def resolve(name: str) -> Exercise | None:
    """Find an exercise by display name.

    Tries the exact name, then the name with punctuation and spacing
    normalised, so "bench press" and "Bench Press" both land.
    """
    exact = _by_lower_name().get(name.lower().strip())
    if exact:
        return exact
    normalised = _normalise(name)
    for e in all_exercises():
        if _normalise(e.name) == normalised:
            return e
    return None


def validate(category: str, exercise_name: str = "") -> Exercise:
    """Check a ``(category, exercise_name)`` pair, or explain what is wrong.

    Raises :class:`InvalidRequest` with near-miss suggestions rather than
    letting Garmin answer with a bare 400.
    """
    cat = category.upper().strip()
    ex = (exercise_name or "").upper().strip()

    if cat not in categories():
        close = difflib.get_close_matches(cat, categories(), n=5, cutoff=0.4)
        raise InvalidRequest(
            f"Unknown exercise category {category!r}.",
            detail=("Did you mean: " + ", ".join(close) + "?")
            if close
            else "Call the exercise catalogue for the valid categories.",
        )

    found = _by_pair().get((cat, ex))
    if found is None:
        in_category = sorted(e.exercise for e in all_exercises() if e.category == cat)
        close = difflib.get_close_matches(ex, in_category, n=5, cutoff=0.4)
        raise InvalidRequest(
            f"Unknown exercise {exercise_name!r} in category {cat}.",
            detail=(
                ("Did you mean: " + ", ".join(close) + "?")
                if close
                else f"Valid names in {cat}: " + ", ".join(in_category[:15])
            ),
        )
    return found


def match(name: str) -> Exercise | None:
    """Best effort: map a free-text exercise name onto the catalogue.

    For callers holding names from somewhere else ("Barbell Bench Press",
    "bench press"). Exact and normalised matches first, then the closest
    fuzzy match above a conservative threshold, so a bad guess returns None
    rather than silently logging the wrong lift.
    """
    direct = resolve(name)
    if direct:
        return direct
    # Compare lowercased, or "bench press" scores badly against the catalogue's
    # "Bench Press" purely on capitalisation.
    lookup = _by_lower_name()
    close = difflib.get_close_matches(name.strip().lower(), list(lookup), n=1, cutoff=0.82)
    return lookup[close[0]] if close else None


def _normalise(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())

"""The strength exercise catalogue.

Read-only and local: the catalogue ships with the package, so these routes
work with no network and without being logged in.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ... import catalogue

router = APIRouter(prefix="/exercises", tags=["exercises"])


@router.get("/search", operation_id="search_exercises")
def search(
    q: str = Query(description="Part of an exercise name, e.g. 'bench' or 'row'."),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, str]]:
    """Find Garmin's name for an exercise.

    Use this before creating a workout or logging a session: every exercise
    must be named with the category and exercise_name pair this returns, and
    Garmin rejects anything else.
    """
    return [e._asdict() for e in catalogue.search(q, limit=limit)]


@router.get("/categories", operation_id="list_exercise_categories")
def categories() -> list[str]:
    """Every exercise category Garmin knows."""
    return list(catalogue.categories())


@router.get("/match", operation_id="match_exercise")
def match(name: str = Query(description="A free-text exercise name.")) -> dict[str, str] | None:
    """Map one free-text exercise name, such as one exported from another app, onto
    exactly one catalogue exercise, or return null when no single match is certain.

    Use it when converting names from another source, where a wrong guess would
    log the wrong lift. To browse candidates and choose, use find_exercise, which
    returns a ranked list. Only a certain match is returned: the exact or
    normalised name, the same words up to a typo, plural or hyphenation, or a
    search with one hit that adds at most an implement (barbell, dumbbell,
    kettlebell). No Garmin request.
    """
    found = catalogue.match(name)
    return found._asdict() if found else None

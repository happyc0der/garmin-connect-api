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
    "pushdown": "press down",
}

# Words that may be missing from a name given elsewhere without making it a
# different lift: "Hack Squat" is the catalogue's "Barbell Hack Squat".
IMPLEMENTS = frozenset({"barbell", "dumbbell", "kettlebell"})


def _words(value: str) -> list[str]:
    """Lowercase alphanumeric words: "Lat Pull-down" -> ["lat", "pull", "down"]."""
    import re

    return re.findall(r"[a-z0-9]+", value.lower())


def _stems(token: str) -> list[str]:
    """What a plural query word may stand for: "presses" -> "press", "flies" -> "fly"."""
    stems = []
    if len(token) >= 5 and token.endswith("ies"):
        stems.append(token[:-3] + "y")
    # "es" is a plural ending only after a hissing sound, or in "flyes": the
    # "es" of "toes" or "lunges" is not, and stripping it would find "to".
    if token.endswith(("sses", "shes", "ches", "xes", "zes", "yes")):
        stems.append(token[:-2])
    if len(token) >= 3 and token.endswith("s"):
        stems.append(token[:-1])
    return stems


def _singular(word: str) -> str:
    """One word's singular, for comparing names: "crushers" -> "crusher"."""
    if len(word) >= 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")) or (
        len(word) >= 5 and word.endswith("yes")
    ):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _starts(word: str, token: str) -> bool:
    """A query word starts a name word, or is its plural: "dips" finds "Dip".

    Only the word as typed may match the start of a name word. A stem with
    its plural ending taken off must be the whole word, or "flies" would
    find "Flip".
    """
    return word.startswith(token) or any(
        word == stem or _singular(word) == stem for stem in _stems(token)
    )


def _query_tokens(term: str) -> list[str]:
    """The query's words, with gym names Garmin does not use replaced."""
    words = _words(term)
    singular = [_singular(w) for w in words]
    phrase = " ".join(singular)
    whole = ALIASES.get(phrase) or ALIASES.get(phrase.replace(" ", ""))
    if whole:
        return _words(whole)
    return _words(" ".join(ALIASES.get(s, w) for w, s in zip(words, singular, strict=True)))


def _ranked(term: str) -> list[tuple[int, Exercise]]:
    """Every match for ``term`` with its rank, best first. 0 is exact, 1 every
    word found, 2 found with spaces and hyphens ignored."""
    tokens = _query_tokens(term)
    if not tokens:
        return []
    joined = [j for j in ["".join(tokens), *_stems("".join(tokens))] if len(j) >= 5]
    # Exact against the words as typed too, before aliases: "Single-leg RDL
    # Circuit" is an exercise's own name, whatever "rdl" stands for elsewhere.
    exact = {
        tuple(_singular(t) for t in tokens),
        tuple(_singular(w) for w in _words(term)),
    }

    scored: list[tuple[int, int, str, Exercise]] = []
    for exercise in all_exercises():
        words = _words(exercise.name)
        flat = "".join(words)
        if tuple(_singular(w) for w in words) in exact:
            rank = 0
        elif all(any(_starts(w, tok) for w in words) for tok in tokens):
            rank = 1
        elif any(j in flat for j in joined):
            rank = 2
        else:
            continue
        scored.append((rank, len(words), exercise.name, exercise))
    scored.sort(key=lambda row: row[:3])
    return [(row[0], row[3]) for row in scored]


def search(term: str, limit: int = 20) -> list[Exercise]:
    """Exercises matching ``term``, best match first.

    A name matches when every word of the query starts one of its words, so
    "pull up" finds "Pull-up" and "lat pulldown" finds "Lat Pull-down", while
    "rdl" no longer matches inside "Hurdle". Plurals are understood, "bench
    presses" and "crunches" included. A query of five or more letters also
    matches with spaces and hyphens ignored, so "pullups" works too. Common
    gym names Garmin does not use, like "skull crushers" or the "rdl" in
    "dumbbell rdl", are aliased.
    """
    return [exercise for _, exercise in _ranked(term)[: max(limit, 0)]]


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
    # A category alone is its generic entry, which every category has.
    ex = (exercise_name or "").upper().strip() or cat

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
    "bench press"). Exact and normalised matches first. Then a close spelling,
    but only one with the same words, allowing a typo inside a word: a
    one-letter change can be a different lift ("Hack Squat" is not "Back
    Squats"). Then a search that finds exactly one exercise. Anything less
    certain returns None rather than silently logging the wrong lift.
    """
    direct = resolve(name)
    if direct:
        return direct
    # Compare lowercased, or "bench press" scores badly against the catalogue's
    # "Bench Press" purely on capitalisation.
    lookup = _by_lower_name()
    query = name.strip().lower()
    for close in difflib.get_close_matches(query, list(lookup), n=3, cutoff=0.82):
        if _same_words(_words(query), _words(close)):
            return lookup[close]
    # The same words joined differently: "pullups" is "Pull-up", "lat
    # pulldowns" is "Lat Pull-down".
    joined = _joined(_query_tokens(name))
    same = [e for e in all_exercises() if _joined(_words(e.name)) == joined]
    if len(same) == 1:
        return same[0]
    # The only exercise search finds, if it adds no more than an implement.
    found = [exercise for rank, exercise in _ranked(name) if rank <= 1]
    if len(found) == 1 and _adds_only_an_implement(_query_tokens(name), _words(found[0].name)):
        return found[0]
    return None


def _significant(words: list[str]) -> list[str]:
    """Singular words, without the "s" of a possessive ("Farmer's" -> "farmer")."""
    return [_singular(w) for w in words if w != "s"]


def _joined(words: list[str]) -> str:
    return "".join(_significant(words))


def _adds_only_an_implement(query: list[str], name: list[str]) -> bool:
    """Every query word is in the name, which has at most one more: an implement."""
    extra = _significant(name)
    for word in _significant(query):
        if word not in extra:
            return False
        extra.remove(word)
    return len(extra) <= 1 and all(word in IMPLEMENTS for word in extra)


def _same_words(ours: list[str], theirs: list[str]) -> bool:
    """Word for word the same, up to plurals and typos.

    A query word that is itself a word the catalogue uses is taken as meant:
    "adduction" is not a typo for "abduction", nor "overhand" for "overhead".
    Any other word may be one edit away, or two in a long word, keeping its
    first letter.
    """
    vocabulary = _vocabulary()

    def same(a: str, b: str) -> bool:
        if _singular(a) == _singular(b):
            return True
        if _singular(a) in vocabulary or a[:1] != b[:1]:
            return False
        return _edits(a, b) <= (2 if len(b) >= 8 else 1)

    return len(ours) == len(theirs) and all(same(a, b) for a, b in zip(ours, theirs, strict=True))


@lru_cache(maxsize=1)
def _vocabulary() -> frozenset[str]:
    """Every word in the catalogue's names, singular."""
    return frozenset(_singular(w) for e in all_exercises() for w in _words(e.name))


def _edits(a: str, b: str) -> int:
    """Edit distance, counting a swap of neighbouring letters as one edit."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _normalise(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())

"""Naming Garmin's personal records, and putting their values in units.

Garmin's record list gives a type id and a bare number. The type list
(``personalrecordtype/prtypes``) gives each id a key such as
``pr.label.1k.run`` and a sport, but no unit, and the unit changes with the
kind of record: seconds for a fastest distance, metres for a longest one,
watts, steps or days. Checked against a real account on 19 Oct 2026: a 1 km
best of 314.83 is 5:14, a longest run of 3045.19 is 3.05 km, a step streak of
326 is days.
"""

from __future__ import annotations

import re
from typing import Any

# Distances in record keys, in metres. Yard pool records are kept in yards in
# the label, as Garmin shows them.
DISTANCES = {
    "1k": ("1 km", 1000.0),
    "1mile": ("1 mile", 1609.344),
    "5k": ("5 km", 5000.0),
    "10k": ("10 km", 10000.0),
    "half.marathon": ("half marathon", 21097.5),
    "full.marathon": ("marathon", 42195.0),
    "40k": ("40 km", 40000.0),
}

SPORT_WORDS = {
    "run": "run",
    "cycle": "ride",
    "poolswim": "pool swim",
    "wheelchair.run": "wheelchair run",
}


def describe(key: str) -> tuple[str, str]:
    """A label and a unit for a record type key.

    ``pr.label.1k.run`` -> ("Fastest 1 km run", "s"). The unit is one of "s",
    "m", "W", "steps", "days" or "kg".
    """
    body = key.removeprefix("pr.label.")

    if body.startswith("max.rep.weight."):
        exercise = body.removeprefix("max.rep.weight.").replace("_", " ")
        return f"Heaviest {exercise}", "kg"
    if body == "max.elev":
        return "Most climbing in a ride", "m"
    if body == "max.power":
        return "Best power in a ride", "W"
    if body.startswith(("steps.", "pushes.")):
        what = "steps" if body.startswith("steps.") else "pushes"
        rest = body.split(".", 1)[1]
        period = {"best.day": "in a day", "best.week": "in a week", "best.month": "in a month"}
        if rest in period:
            return f"Most {what} {period[rest]}", what if what == "steps" else "pushes"
        noun = what.removesuffix("s")
        if rest == "longest.streak":
            return f"Longest {noun} goal streak", "days"
        if rest == "current.streak":
            return f"Current {noun} goal streak", "days"
    if body == "activeseconds.best.day":
        return "Most active time in a day", "s"
    if body.startswith("farthest."):
        return f"Longest {SPORT_WORDS.get(body.removeprefix('farthest.'), body)}", "m"
    if body.startswith("longest."):
        thing = body.removeprefix("longest.").replace(".", " ")
        return f"Longest {SPORT_WORDS.get(body.removeprefix('longest.'), thing)}", "m"

    # Fastest over a distance: "<distance>.<sport>", or a pool distance.
    pool = re.fullmatch(r"(\d+)(m|yd)\.poolswim", body)
    if pool:
        return f"Fastest {pool.group(1)} {pool.group(2)} pool swim", "s"
    for prefix, (label, _) in DISTANCES.items():
        if body == prefix or body.startswith(prefix + "."):
            sport = body[len(prefix) :].lstrip(".") or "run"
            if prefix in ("half.marathon", "full.marathon") and not sport:
                return f"Fastest {label}", "s"
            word = SPORT_WORDS.get(sport, sport.replace(".", " "))
            return (
                f"Fastest {label}"
                if word == "run" and "marathon" in prefix
                else f"Fastest {label} {word}"
            ), "s"
    return body.replace(".", " ").capitalize(), ""


def convert(value: float | None, unit: str) -> float | None:
    """Garmin's raw value in the stated unit.

    Strength records are inferred to be grams, as Garmin stores strength
    weights elsewhere; no account checked had one.
    """
    if value is None:
        return None
    if unit == "kg" and value > 1000:
        return round(value / 1000.0, 2)
    return value


def display(value: float | None, unit: str) -> str | None:
    """A human reading: 5:14, 3.05 km, 152 m, 101,690 steps, 326 days."""
    if value is None:
        return None
    if unit == "s":
        # Tenths kept, never rounded up: 314.83 is 5:14.8, not 5:15.
        tenths = int(value * 10)
        whole, tenth = divmod(tenths, 10)
        hours, rest = divmod(whole, 3600)
        minutes, seconds = divmod(rest, 60)
        clock = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        return f"{clock}.{tenth}" if tenth else clock
    if unit == "m":
        return f"{value / 1000:.2f} km" if value >= 1000 else f"{value:.0f} m"
    if unit in ("steps", "pushes"):
        return f"{value:,.0f} {unit}"
    if unit == "days":
        return f"{value:.0f} days"
    if unit == "W":
        return f"{value:.0f} W"
    if unit == "kg":
        return f"{value:g} kg"
    return f"{value:g}"


def index(types: list[dict[str, Any]] | None) -> dict[int, dict[str, Any]]:
    """Type id -> {key, sport, label, unit}."""
    out: dict[int, dict[str, Any]] = {}
    for entry in types or []:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        label, unit = describe(str(entry.get("key", "")))
        out[int(entry["id"])] = {
            "key": entry.get("key"),
            "sport": entry.get("sport"),
            "label": label,
            "unit": unit,
        }
    return out

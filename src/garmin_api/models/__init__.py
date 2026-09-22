"""The public contract.

These models are what the library returns, what the HTTP service serialises and
what the MCP tools describe. Garmin's own JSON is inconsistent -- camelCase
mixed with snake_case, weights in grams here and kilograms there, timestamps as
epoch milliseconds, ISO strings, or local-time strings with no offset -- so
nothing raw escapes this layer without being normalised first.

Every response model keeps a ``raw`` escape hatch for the fields not mapped, so
using this package never means losing access to something Garmin sent.
"""

from .activities import (
    Activity,
    ActivitySets,
    ActivitySummary,
    ActivityType,
    StrengthSet,
)
from .common import Paging
from .health import BodyComposition, DaySummary, SleepSummary, WeighIn
from .workouts import (
    ScheduledWorkout,
    StrengthExerciseSpec,
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
    StrengthWorkoutSpec,
    WorkoutSummary,
)

__all__ = [
    "Activity",
    "ActivitySets",
    "ActivitySummary",
    "ActivityType",
    "BodyComposition",
    "DaySummary",
    "Paging",
    "ScheduledWorkout",
    "SleepSummary",
    "StrengthExerciseSpec",
    "StrengthSessionExercise",
    "StrengthSessionSet",
    "StrengthSessionSpec",
    "StrengthSet",
    "StrengthWorkoutSpec",
    "WeighIn",
    "WorkoutSummary",
]

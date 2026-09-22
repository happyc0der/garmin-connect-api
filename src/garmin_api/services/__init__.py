"""The API proper: typed functions over Garmin Connect.

Import these directly to use this package as a library. The HTTP service and
the MCP server are both thin wrappers over exactly these functions, so all
three faces behave identically.
"""

from . import activities, health, uploads, weight, workouts

__all__ = ["activities", "health", "uploads", "weight", "workouts"]

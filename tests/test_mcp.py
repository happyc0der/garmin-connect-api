"""The MCP server that is generated from the HTTP app."""

from __future__ import annotations

import pytest

from garmin_api.mcp import create_mcp

# Tools a client is expected to find. Losing one silently would break a
# conversation that depends on it, so the set is asserted rather than counted.
EXPECTED = {
    "list_activities",
    "get_activity",
    "get_activity_sets",
    "get_day_summary",
    "get_sleep",
    "list_workouts",
    "create_strength_workout",
    "schedule_workout",
    "push_workout_to_device",
    "log_strength_session",
    "search_exercises",
    "add_weigh_in",
    "garmin_status",
    "find_exercise",
    "recent_strength_sessions",
    "week_summary",
}


@pytest.fixture(scope="module")
def tools() -> dict[str, object]:
    import asyncio

    listed = asyncio.run(create_mcp().list_tools())
    return {tool.name: tool for tool in listed}


def test_every_expected_tool_is_present(tools: dict[str, object]) -> None:
    assert set(tools) >= EXPECTED


def test_bulky_endpoints_are_not_tools(tools: dict[str, object]) -> None:
    """Sample-level data would flood a context window and help nobody."""
    assert "get_activity_splits" not in tools
    assert "download_activity_file" not in tools


def test_tools_carry_descriptions(tools: dict[str, object]) -> None:
    """A tool with no description is a tool a model will misuse."""
    undocumented = [name for name, tool in tools.items() if not getattr(tool, "description", None)]
    assert undocumented == []


def test_status_tool_works_without_tokens() -> None:
    """The first thing to call when something fails must not itself need Garmin."""
    import asyncio

    result = asyncio.run(create_mcp().call_tool("garmin_status", {}))
    payload = result.structured_content or {}
    assert payload.get("authenticated") is False
    assert "garmin login" in (payload.get("hint") or "")


def test_instructions_warn_about_rate_limits() -> None:
    mcp = create_mcp()
    assert "rate limiting" in (mcp.instructions or "")

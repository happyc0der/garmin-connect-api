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
    "add_weigh_in",
    "garmin_status",
    "find_exercise",
    "recent_strength_sessions",
    "week_summary",
    "get_activity_laps",
    "edit_activity_sets",
    "create_endurance_workout",
    "get_trend",
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


def test_every_tool_says_what_it_does_to_the_account(tools: dict[str, object]) -> None:
    """Clients use these hints to decide what may run without asking."""
    missing = [name for name, tool in tools.items() if getattr(tool, "annotations", None) is None]
    assert missing == []


def test_deleting_and_overwriting_tools_are_marked_destructive(tools: dict[str, object]) -> None:
    destructive = {
        name
        for name, tool in tools.items()
        if tool.annotations.destructive_hint  # type: ignore[attr-defined]
    }
    assert destructive == {
        "delete_activity",
        "delete_workout",
        "delete_weigh_in",
        "unschedule_workout",
        "update_activity",
        "edit_activity_sets",
    }


def test_reading_tools_are_marked_read_only(tools: dict[str, object]) -> None:
    for name in ("list_activities", "get_day_summary", "get_activity_sets", "week_summary"):
        assert tools[name].annotations.read_only_hint is True, name  # type: ignore[attr-defined]
    for name in ("log_strength_session", "create_strength_workout", "add_body_composition"):
        assert tools[name].annotations.read_only_hint is False, name  # type: ignore[attr-defined]


def test_only_garmin_calls_are_open_world(tools: dict[str, object]) -> None:
    """The catalogue and status answer offline; everything else reaches Garmin."""
    local = {name for name, tool in tools.items() if tool.annotations.open_world_hint is False}  # type: ignore[attr-defined]
    assert local == {"garmin_status", "find_exercise", "match_exercise"}


def test_duplicate_tools_stay_off_the_mcp_surface(tools: dict[str, object]) -> None:
    """Two names for one thing only make a model choose; they stay on the HTTP API."""
    assert not {"search_exercises", "healthz", "get_last_activity"} & set(tools)


def _undescribed(schema: object, root: dict, path: str, seen: frozenset[str] = frozenset()):
    """Properties without a description, however deeply nested."""
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref not in seen:
            node = root
            for part in ref.removeprefix("#/").split("/"):
                node = node[part]
            yield from _undescribed(node, root, path, seen | {ref})
        return
    for name, prop in schema.get("properties", {}).items():
        if not prop.get("description"):
            yield f"{path}.{name}"
        yield from _undescribed(prop, root, f"{path}.{name}", seen)
    yield from _undescribed(schema.get("items"), root, f"{path}[]", seen)
    for option in schema.get("anyOf", []) + schema.get("oneOf", []):
        yield from _undescribed(option, root, path, seen)


def test_every_tool_argument_is_described(tools: dict[str, object]) -> None:
    """A model fills in what it understands, nested fields included; Glama grades the same."""
    undescribed = [
        field
        for name, tool in tools.items()
        for field in _undescribed(tool.parameters or {}, tool.parameters or {}, name)  # type: ignore[attr-defined]
    ]
    assert undescribed == []


def test_every_tool_has_a_readable_title(tools: dict[str, object]) -> None:
    """Directories require a title; derived from the operation id, not FastAPI's "Delete"."""
    titles = {name: tool.annotations.title for name, tool in tools.items()}  # type: ignore[attr-defined]
    assert all(titles.values())
    assert titles["delete_activity"] == "Delete activity"
    assert titles["get_hrv"] == "Get HRV"
    assert titles["add_weigh_in"] == "Add weigh-in"
    assert titles["garmin_status"] == "Garmin status"

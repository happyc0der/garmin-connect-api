"""Read-only checks against the real account. Safe to run; they change nothing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from garmin_api import auth
from garmin_api.services import activities, health


@pytest.fixture(autouse=True)
def require_login() -> None:
    if not auth.has_tokens():
        pytest.skip("No tokens. Run `garmin login` first.")


def test_whoami() -> None:
    assert auth.client().get_full_name()


def test_list_recent_activities() -> None:
    found = activities.list_activities(limit=5)
    assert isinstance(found, list)
    for activity in found:
        assert activity.activity_id > 0


def test_read_sets_of_the_latest_strength_activity() -> None:
    found = activities.list_activities(limit=1, activity_type=activities.STRENGTH)
    if not found:
        pytest.skip("No strength activities on this account.")

    result = activities.get_sets(found[0].activity_id)
    assert result.sets, "a strength activity should have sets"
    for entry in result.sets:
        assert entry.exercise_category
    assert result.total_volume_kg >= 0


def test_day_summary_for_yesterday() -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    summary = health.day(yesterday)
    assert summary.day == yesterday

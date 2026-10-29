"""A trend reaching today leaves a day still filling out of its summary (issue #11).

Dates are relative to the real date: whether a range can reach today is
first judged against the machine's clock, so fixed dates would age out.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from garmin_api.services import trends

TODAY = date.today()


def _steps(fake: dict[str, Any], today: date, values: list[int]) -> list[date]:
    """Daily steps ending on ``today``, which the account also says is today."""
    days = [today - timedelta(days=len(values) - 1 - i) for i in range(len(values))]
    fake["responses"].update(
        {
            "today": today,
            "get_daily_steps": [
                {"calendarDate": d.isoformat(), "totalSteps": v}
                for d, v in zip(days, values, strict=True)
            ],
        }
    )
    return days


def test_a_mornings_steps_are_not_a_day(fake_garmin: dict[str, Any]) -> None:
    """At 9am, today's 600 steps would be the minimum and a 7,400-step drop."""
    days = _steps(fake_garmin, TODAY, [8000] * 29 + [600])
    trend = trends.trend("steps", days[0], TODAY, aggregation="day")

    assert (trend.summary["min"], trend.summary["change"]) == (8000, 0)
    assert trend.summary["days_with_data"] == 29
    assert trend.summary["last"]["day"] == (TODAY - timedelta(days=1)).isoformat()
    assert trend.summary["in_progress"] == TODAY.isoformat()
    assert trend.points[-1].value == 600, "today's count is still shown"


def test_weekly_steps_ending_today_compare_whole_weeks(fake_garmin: dict[str, Any]) -> None:
    sunday = TODAY + timedelta(days=6 - TODAY.weekday())
    days = _steps(fake_garmin, sunday, [8000] * 27 + [600])
    trend = trends.trend("steps", days[0], sunday, aggregation="week")
    assert trend.summary["change"] == 0
    assert trend.summary["in_progress"] == sunday.isoformat()


def test_two_days_ending_today_do_not_compare_against_the_morning(
    fake_garmin: dict[str, Any],
) -> None:
    days = _steps(fake_garmin, TODAY, [8000, 600])
    trend = trends.trend("steps", days[0], TODAY, aggregation="day")
    assert (trend.summary["change"], trend.summary["days_with_data"]) == (0, 1)


def test_today_alone_still_answers(fake_garmin: dict[str, Any]) -> None:
    _steps(fake_garmin, TODAY, [600])
    trend = trends.trend("steps", TODAY, TODAY, aggregation="day")
    assert trend.summary["last"]["value"] == 600
    assert trend.summary["in_progress"] == TODAY.isoformat()


def test_a_range_ending_yesterday_is_untouched(fake_garmin: dict[str, Any]) -> None:
    yesterday = TODAY - timedelta(days=1)
    days = _steps(fake_garmin, yesterday, [8000] * 9 + [600])
    fake_garmin["responses"]["today"] = TODAY
    trend = trends.trend("steps", days[0], yesterday, aggregation="day")
    assert trend.summary["min"] == 600, "yesterday is finished, low or not"
    assert "in_progress" not in trend.summary


def test_a_past_range_does_not_ask_what_today_is(fake_garmin: dict[str, Any]) -> None:
    _steps(fake_garmin, date(2025, 1, 31), [8000] * 31)
    del fake_garmin["responses"]["today"]
    trends.trend("steps", "2025-01-01", "2025-01-31", aggregation="day")
    assert "today" not in [name for name, _ in fake_garmin["calls"]]


@pytest.mark.parametrize("hours", [7.5])
def test_last_nights_sleep_is_kept(fake_garmin: dict[str, Any], hours: float) -> None:
    """Sleep is final by morning: today's is the night just ended, not a running count."""
    days = [TODAY - timedelta(days=1), TODAY]
    fake_garmin["responses"].update(
        {
            "get_sleep_daily": [
                {"calendarDate": d.isoformat(), "values": {"totalSleepTimeInSeconds": hours * 3600}}
                for d in days
            ]
        }
    )
    trend = trends.trend("sleep", days[0], TODAY, aggregation="day")
    assert trend.summary["last"]["day"] == TODAY.isoformat()
    assert "in_progress" not in trend.summary
    assert "today" not in [name for name, _ in fake_garmin["calls"]]

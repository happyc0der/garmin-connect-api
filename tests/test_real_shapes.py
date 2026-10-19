"""Shapes captured from a real account on 19 Oct 2026, and what is made of them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from garmin_api.errors import InvalidRequest
from garmin_api.fit.body import decode_body_composition, encode_body_composition
from garmin_api.models.health import BodyCompositionSpec
from garmin_api.services import activities, health, weight, workouts

# Fetching one activity nests everything, unlike the list endpoint.
ACTIVITY_DETAIL = {
    "activityId": 18800000001,
    "activityName": "Pool Swim",
    "activityTypeDTO": {"typeId": 27, "typeKey": "lap_swimming", "parentTypeId": 26},
    "summaryDTO": {
        "startTimeGMT": "2026-08-30T22:15:00.0",
        "startTimeLocal": "2026-08-30T18:15:00.0",
        "duration": 2600.5,
        "distance": 2450.5,
        "calories": 260.0,
        "averageHR": 135.0,
        "maxHR": 168.0,
    },
}

# 3-minute stress readings: -1 and -2 mean no reading.
START = 1792296000000
STRESS = {
    "calendarDate": "2026-10-18",
    "avgStressLevel": 33,
    "maxStressLevel": 88,
    "stressValuesArray": [
        [START + i * 180000, level]
        for i, level in enumerate([-1, -2, 10, 25, 26, 50, 51, 75, 76, 97])
    ],
}


def test_a_fetched_activity_keeps_its_type_and_start(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_activity"] = ACTIVITY_DETAIL
    found = activities.get_activity(18800000001)

    assert found.activity_type is not None
    assert found.activity_type.type_key == "lap_swimming"
    assert found.start_time == datetime(2026, 8, 30, 22, 15, 0, tzinfo=UTC)
    assert found.start_time_local == "2026-08-30T18:15:00"
    assert found.distance_m == 2450.5
    assert found.raw is not None and "summaryDTO" in found.raw, "raw stays Garmin's original"


def test_stress_is_reduced_to_minutes_per_band(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_stress_data"] = STRESS
    summary = health.stress_summary("2026-10-18")

    assert summary is not None
    assert (summary.average, summary.max) == (33, 88)
    # Two readings in each band, three minutes apiece.
    assert summary.rest_minutes == 6
    assert summary.low_minutes == 6
    assert summary.medium_minutes == 6
    assert summary.high_minutes == 6
    assert summary.unmeasured_minutes == 6
    assert summary.samples is None, "readings only when asked for"


def test_stress_samples_on_request(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_stress_data"] = STRESS
    summary = health.stress_summary("2026-10-18", include_samples=True)
    assert summary is not None and summary.samples is not None
    assert len(summary.samples) == 10


def test_steps_are_summed_by_hour(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_steps_data"] = [
        {"startGMT": "2026-10-18T14:00:00.0", "steps": 8},
        {"startGMT": "2026-10-18T14:15:00.0", "steps": 92},
        {"startGMT": "2026-10-18T15:00:00.0", "steps": 400},
    ]
    summary = health.steps_summary("2026-10-18")
    assert summary.total == 500
    assert summary.hourly_gmt[14] == 100
    assert summary.hourly_gmt[15] == 400
    assert len(summary.hourly_gmt) == 24


def test_hrv_summary_drops_readings(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_hrv_data"] = {
        "hrvSummary": {
            "calendarDate": "2026-10-18",
            "weeklyAvg": 70,
            "lastNightAvg": 61,
            "lastNight5MinHigh": 95,
            "baseline": {"lowUpper": 77, "balancedLow": 81, "balancedUpper": 97},
            "status": "LOW",
        },
        "hrvReadings": [{"hrvValue": 50}] * 78,
    }
    summary = health.hrv_summary("2026-10-18")
    assert summary is not None
    assert (summary.last_night_avg, summary.weekly_avg, summary.status) == (61, 70, "LOW")
    assert summary.baseline_balanced_low == 81
    assert summary.readings is None


def test_devices_are_summarised(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_devices"] = [
        {
            "deviceId": 3500000001,
            "displayName": "Example Watch",
            "productDisplayName": "Example Watch",
            "partNumber": "006-B0000-00",
            "primaryActivityTrackerIndicator": True,
            "deviceStatus": "active",
            **{f"capability{i}Capable": True for i in range(300)},
        }
    ]
    found = workouts.devices()
    assert found[0].device_id == 3500000001
    assert found[0].primary is True
    assert len(found[0].model_dump()) < 10, "the capability flags are gone"


def test_records_keep_garmins_type_id(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_personal_record"] = [
        {
            "id": 2700000001,
            "typeId": 1,
            "value": 287.5,
            "activityType": "running",
            "activityId": 18800000003,
            "activityName": "Morning Run",
            "prStartTimeGmtFormatted": "2025-09-14T13:20:00.0",
            "prTypeLabelKey": None,
        }
    ]
    found = health.records()
    assert found[0].type_id == 1
    assert found[0].achieved_at == datetime(2025, 9, 14, 13, 20, 0, tzinfo=UTC)


def test_weigh_ins_carry_the_id_delete_needs(fake_garmin: dict[str, Any]) -> None:
    fake_garmin["responses"]["get_body_composition"] = {
        "dateWeightList": [
            {
                "samplePk": 1771000000001,
                "calendarDate": "2026-02-16",
                "timestampGMT": 1771290000000,
                "weight": 72400.0,
                "metabolicAge": 24,
                "sourceType": "MANUAL",
            }
        ]
    }
    found = weight.weigh_ins("2026-01-01", "2026-10-18")
    assert found[0].sample_pk == 1771000000001
    assert found[0].weight_kg == 72.4
    assert found[0].metabolic_age == 24


class TestDayParts:
    STUBS = {
        "get_stats": {"totalSteps": 1},
        "get_sleep_data": None,
        "get_hrv_data": None,
        "get_training_readiness": None,
        "get_daily_weigh_ins": None,
    }

    def test_a_full_day_is_five_requests(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"].update(self.STUBS)
        health.day("2026-10-18")
        assert len(fake_garmin["calls"]) == 5

    def test_parts_limit_the_requests(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"].update(self.STUBS)
        health.day("2026-10-18", parts={"stats", "sleep", "readiness"})
        assert sorted(name for name, _ in fake_garmin["calls"]) == [
            "get_sleep_data",
            "get_stats",
            "get_training_readiness",
        ]

    def test_an_unknown_part_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Unknown day parts"):
            health.day("2026-10-18", parts={"stats", "vibes"})

    def test_a_week_summary_stays_within_budget(self, fake_garmin: dict[str, Any]) -> None:
        """Seven days at three requests each, plus the activity list."""
        import asyncio

        from garmin_api.mcp import create_mcp

        fake_garmin["responses"].update(self.STUBS)
        fake_garmin["responses"]["get_activities_by_date"] = []
        asyncio.run(create_mcp().call_tool("week_summary", {"end_day": "2026-10-18"}))
        assert len(fake_garmin["calls"]) <= 7 * 3 + 1


class TestBodyCompositionFit:
    SPEC = BodyCompositionSpec(
        timestamp=datetime(2026, 10, 19, 7, 30, tzinfo=timezone(timedelta(hours=-4))),
        weight_kg=72.4,
        percent_fat=18.4,
        percent_hydration=55.2,
        bone_mass_kg=3.1,
        muscle_mass_kg=34.25,
        basal_met_kcal=1690,
        visceral_fat_rating=7,
        metabolic_age=24,
        bmi=22.9,
    )

    def test_round_trip_is_lossless(self) -> None:
        back = decode_body_composition(encode_body_composition(self.SPEC))[0]
        assert back.model_dump(exclude_none=True) == {
            **self.SPEC.model_dump(exclude_none=True),
            "timestamp": self.SPEC.timestamp,
        }

    def test_the_instant_survives_any_machine_timezone(self, monkeypatch) -> None:
        """The upstream encoder used time.mktime, which assumes the machine's zone."""
        import time

        monkeypatch.setenv("TZ", "UTC")
        time.tzset()
        try:
            back = decode_body_composition(encode_body_composition(self.SPEC))[0]
        finally:
            monkeypatch.delenv("TZ")
            time.tzset()
        assert back.timestamp == datetime(2026, 10, 19, 11, 30, tzinfo=UTC)

    def test_the_same_weigh_in_encodes_identically(self) -> None:
        assert encode_body_composition(self.SPEC) == encode_body_composition(self.SPEC)

    def test_a_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            BodyCompositionSpec(timestamp=datetime(2026, 10, 19, 7, 30), weight_kg=72.4)

    def test_a_file_with_no_weigh_ins_says_so(self) -> None:
        from tests.test_fit_codec import encode_minimal_non_strength

        with pytest.raises(InvalidRequest, match="No weigh-ins"):
            decode_body_composition(encode_minimal_non_strength())

    def test_upload_goes_through_the_upload_service(self, fake_garmin: dict[str, Any]) -> None:
        fake_garmin["responses"]["upload_activity"] = {
            "detailedImportResult": {"successes": [{"internalId": 9}], "failures": []}
        }
        result = weight.add_body_composition(self.SPEC)
        assert result["status"] == "created"
        assert fake_garmin["calls"][0][0] == "upload_activity"
        assert fake_garmin["calls"][0][1][0].endswith(".fit")


@pytest.mark.parametrize(
    ("stored", "years"),
    [(757366224000, 24), (946707780000.0, 30), (1577846300000.0, 50), (31, 31), (None, None)],
)
def test_metabolic_age_is_decoded_from_milliseconds(stored: Any, years: int | None) -> None:
    """Values captured live after writing ages 24, 30 and 50."""
    from garmin_api.models.health import WeighIn

    assert WeighIn.from_garmin({"weight": 72400.0, "metabolicAge": stored}).metabolic_age == years

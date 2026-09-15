"""Tests for Anode coordinators and schedule calculations."""
from __future__ import annotations

from datetime import UTC, datetime, time
from http import HTTPStatus
from zoneinfo import ZoneInfo

import pytest

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.api import OperatingMode, PowerLimitKey, ScheduleSlot
from custom_components.anode_battery.coordinator import (
    next_schedule_change,
    scheduled_mode_at,
)

from .common import (
    MAX_CHARGE,
    METADATA,
    SOC_CONFIG,
    STATUS,
    AnodeCloud,
    load_fixture,
    refresh,
)

LONDON = ZoneInfo("Europe/London")
SCHEDULE = (
    ScheduleSlot(time(23), time(2), OperatingMode.CHARGE),
    ScheduleSlot(time(2), time(5), OperatingMode.CHARGE),
    ScheduleSlot(time(17), time(19), OperatingMode.DISCHARGE),
)


@pytest.fixture(autouse=True)
async def london(hass: HomeAssistant) -> None:
    """Run schedule maths in a time zone with daylight saving."""
    await hass.config.async_set_time_zone("Europe/London")


def local(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=LONDON)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (local(23, 30), OperatingMode.CHARGE),
        (local(1), OperatingMode.CHARGE),
        (local(5), OperatingMode.MATCH),
        (local(18), OperatingMode.DISCHARGE),
    ],
)
def test_scheduled_mode_at(moment: datetime, expected: OperatingMode) -> None:
    """The scheduled mode follows slot boundaries, including overnight."""
    assert scheduled_mode_at(SCHEDULE, moment)[0] is expected


def test_next_change_skips_boundaries_that_keep_the_mode() -> None:
    """Back-to-back slots with the same mode are one continuous charge."""
    change = next_schedule_change(SCHEDULE, local(23, 30).astimezone(UTC))
    assert change == (local(5, day=16), OperatingMode.MATCH)


def test_next_change_wraps_to_tomorrow() -> None:
    """After the last change of the day, the next is tomorrow's first."""
    assert next_schedule_change(SCHEDULE, local(20)) == (local(23), OperatingMode.CHARGE)
    assert next_schedule_change(SCHEDULE, local(19)) == (local(23), OperatingMode.CHARGE)


def test_no_schedule() -> None:
    """Without a schedule nothing changes and the hub matches load."""
    assert next_schedule_change((), local(12)) is None
    assert scheduled_mode_at((), local(12)) == (OperatingMode.MATCH, None)


async def test_auth_failure_during_polling_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A key revoked while running asks the user to re-authenticate."""
    cloud.respond("GET", STATUS, status=HTTPStatus.UNAUTHORIZED)
    await refresh(hass, init_integration.runtime_data.status)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_metadata_failure_keeps_aliases(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Metadata is optional: failures keep the last aliases and never reauth."""
    cloud.respond("GET", METADATA, status=HTTPStatus.UNAUTHORIZED)
    status = init_integration.runtime_data.status
    await refresh(hass, status)
    assert status.last_update_success
    assert status.data.batteries["bat01"].alias == "Garage battery"
    assert status.data.grid_meters[0].id == "grid1"
    assert hass.config_entries.flow.async_progress() == []


async def test_settings_partial_failure_keeps_previous(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """One failed settings read keeps its last value and updates the rest."""
    settings = init_integration.runtime_data.settings
    soc = load_fixture("config_soc.json")
    soc["value"][0]["config"]["minSoc"] = 30
    cloud.respond("GET", SOC_CONFIG, json=soc)
    cloud.respond("GET", MAX_CHARGE, status=HTTPStatus.REQUEST_TIMEOUT)
    await refresh(hass, settings)

    assert settings.last_update_success
    assert settings.data.soc_limits["bat01"].min_soc == 30
    assert settings.data.power_limits[PowerLimitKey.MAX_CHARGE].watts == 4400

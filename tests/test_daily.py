"""Tests for the energy today sensors."""
from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.anode_battery.const import DOMAIN

from .common import HUB_ID, METERS, AnodeCloud, load_fixture, refresh, state

TODAY_KEYS = (
    "battery_charge_energy_today",
    "battery_discharge_energy_today",
    "grid_import_energy_today",
    "grid_export_energy_today",
    "house_energy_today",
)
GRID_TODAY = "grid_import_energy_today"


def today(hass: HomeAssistant, key: str = GRID_TODAY) -> float:
    return float(state(hass, "sensor", f"{HUB_ID}_{key}").state)


def set_grid_import(cloud: AnodeCloud, kwh: float) -> None:
    """Serve a grid meter import counter of this many kWh (fixture starts at 500)."""
    meters = load_fixture("meters.json")
    meters[0]["importEnergy"]["value"] = round(kwh * 10000)
    cloud.respond("GET", METERS, json=meters)


def existing_entity(hass: HomeAssistant, entry: MockConfigEntry, key: str = GRID_TODAY) -> str:
    """Register an entity as an earlier install would have."""
    return (
        er.async_get(hass)
        .async_get_or_create(
            "sensor",
            DOMAIN,
            f"{HUB_ID}_{key}",
            suggested_object_id=f"anode_hub_{HUB_ID}_{key}",
            config_entry=entry,
        )
        .entity_id
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("frozen_time")
async def test_counts_from_first_reading(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A new install starts every today sensor at zero and counts up."""
    for key in TODAY_KEYS:
        assert today(hass, key) == 0

    set_grid_import(cloud, 510)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert today(hass, GRID_TODAY) == 10
    assert today(hass, "house_energy_today") == 10
    assert today(hass, "grid_export_energy_today") == 0


async def test_resets_at_local_midnight(
    hass: HomeAssistant,
    frozen_time: datetime,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """Today's figure returns to zero at midnight in Home Assistant's time zone."""
    set_grid_import(cloud, 504)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert today(hass) == 4

    # Midnight in London (BST) is 23:00 UTC.
    freezer.move_to(datetime.fromisoformat("2026-09-15T23:00:00+00:00"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert today(hass) == 0

    set_grid_import(cloud, 506)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert today(hass) == 2


@pytest.mark.usefixtures("frozen_time")
async def test_total_drop_keeps_todays_figure(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A counter reset keeps today's figure and counting carries on."""
    telemetry = init_integration.runtime_data.telemetry
    set_grid_import(cloud, 504)
    await refresh(hass, telemetry)
    assert today(hass) == 4

    set_grid_import(cloud, 100)
    await refresh(hass, telemetry)
    assert today(hass) == 4

    set_grid_import(cloud, 101)
    await refresh(hass, telemetry)
    assert today(hass) == 5


@pytest.mark.usefixtures("frozen_time")
async def test_unavailable_while_readings_fail(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """No readings means unavailable; counting resumes from the same baseline."""
    telemetry = init_integration.runtime_data.telemetry
    cloud.respond("GET", METERS, status=HTTPStatus.REQUEST_TIMEOUT)
    await refresh(hass, telemetry)
    assert state(hass, "sensor", f"{HUB_ID}_{GRID_TODAY}").state == STATE_UNAVAILABLE

    set_grid_import(cloud, 503)
    await refresh(hass, telemetry)
    assert today(hass) == 3


@pytest.mark.usefixtures("frozen_time")
async def test_restart_same_day(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """After a restart the baseline is kept, so energy used while stopped counts."""
    mock_config_entry.add_to_hass(hass)
    entity_id = existing_entity(hass, mock_config_entry)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "5.0"),
                {"today": 5.0, "baseline": 490.0, "last_total": 495.0, "day": "2026-09-15"},
            )
        ],
    )
    await setup(hass, mock_config_entry)
    assert float(hass.states.get(entity_id).state) == 10


@pytest.mark.usefixtures("frozen_time")
async def test_restart_next_day(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Restarting on a new day starts from the last total seen before stopping."""
    mock_config_entry.add_to_hass(hass)
    entity_id = existing_entity(hass, mock_config_entry)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "7.0"),
                {"today": 7.0, "baseline": 488.0, "last_total": 495.0, "day": "2026-09-14"},
            )
        ],
    )
    await setup(hass, mock_config_entry)
    assert float(hass.states.get(entity_id).state) == 5


@pytest.mark.usefixtures("frozen_time")
async def test_upgrade_keeps_todays_figure(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """The baseline earlier releases stored in attributes is picked up."""
    mock_config_entry.add_to_hass(hass)
    entity_id = existing_entity(hass, mock_config_entry)
    mock_restore_cache(
        hass,
        [State(entity_id, "3.0", {"baseline_kwh": 495.0, "last_reset_day": "2026-09-15"})],
    )
    await setup(hass, mock_config_entry)
    assert float(hass.states.get(entity_id).state) == 5

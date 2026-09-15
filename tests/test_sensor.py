"""Tests for Anode sensors."""
from __future__ import annotations

from datetime import datetime, timedelta
from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.anode_battery.const import DOMAIN

from .common import BATTERIES, HUB_ID, METERS, MODE, STATUS, AnodeCloud, load_fixture, refresh, state


def value(hass: HomeAssistant, unique_id: str) -> float:
    """Numeric state of a sensor."""
    return float(state(hass, "sensor", unique_id).state)


async def test_battery_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Battery readings are exposed with normalised units."""
    assert value(hass, "bat01_power") == 1500
    assert value(hass, "bat01_import_power") == 1500
    assert value(hass, "bat01_export_power") == 0
    assert value(hass, "bat02_import_power") == 0
    assert value(hass, "bat02_export_power") == 500
    assert state(hass, "sensor", "bat01_soc").state == "75"
    assert value(hass, "bat01_capacity") == 136
    assert value(hass, "bat01_capacity_remaining") == 102
    assert value(hass, "bat01_energy_capacity") == 6038.4
    assert value(hass, "bat01_energy_remaining") == 4528.8
    assert value(hass, "bat01_nominal_voltage") == 44.4
    assert state(hass, "sensor", "bat01_power_status").state == "CHARGING"
    assert value(hass, "bat01_charge_energy") == pytest.approx(922.691)
    assert value(hass, "bat01_discharge_energy") == pytest.approx(730.428)
    assert state(hass, "sensor", "bat01_version").state == "1.3.0"


async def test_meter_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Meter readings are exposed with normalised units."""
    assert value(hass, "grid1_power") == 2000
    assert value(hass, "grid1_import_energy") == 500
    assert value(hass, "grid1_export_energy") == 30
    assert value(hass, "solar1_export_power") == 2500
    assert state(hass, "sensor", "grid1_type").state == "PRIMARY"
    assert state(hass, "sensor", "ev001_parent_meter").state == "grid1"


async def test_hub_totals(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Hub sensors combine every device."""
    assert value(hass, f"{HUB_ID}_battery_energy_capacity") == 10838.4
    assert value(hass, f"{HUB_ID}_battery_energy_remaining") == 6928.8
    assert state(hass, "sensor", f"{HUB_ID}_average_soc").state == "64"
    assert value(hass, f"{HUB_ID}_battery_cumulative_charge_energy") == pytest.approx(1422.691)
    assert value(hass, f"{HUB_ID}_battery_cumulative_discharge_energy") == pytest.approx(1130.428)
    # 2000 grid - (-2500) solar - (1500 + -500) batteries
    assert value(hass, f"{HUB_ID}_house_power") == 3500
    # (500 - 30) grid + (80 - 1) solar + (730.428 - 922.691) + (400 - 500) batteries
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(256.737)
    assert value(hass, f"{HUB_ID}_grid_import_energy") == pytest.approx(500)
    assert value(hass, f"{HUB_ID}_grid_export_energy") == pytest.approx(30)


async def test_meter_electrical_sensors_disabled_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Voltage, current and power factor exist but start disabled."""
    registry = er.async_get(hass)
    ids = {
        key: registry.async_get_entity_id("sensor", DOMAIN, f"grid1_{key}")
        for key in ("voltage", "current", "power_factor")
    }
    for found in ids.values():
        assert found is not None
        assert registry.async_get(found).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    for found in ids.values():
        registry.async_update_entity(found, disabled_by=None)
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(ids["voltage"]).state) == 240.1
    assert float(hass.states.get(ids["current"]).state) == 8.3
    assert float(hass.states.get(ids["power_factor"]).state) == 0.98
    assert hass.states.get(ids["voltage"]).name == "Grid Voltage"


async def test_entity_names_follow_devices(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Entity names are the device name plus what is measured."""
    assert state(hass, "sensor", "bat01_soc").name == "Garage battery State of charge"
    assert state(hass, "sensor", "bat01_power").name == "Garage battery Power"
    assert state(hass, "sensor", f"{HUB_ID}_house_energy").name == "Home hub House energy"


async def test_battery_dropout(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A battery missing from a read is unavailable, never zero.

    Lifetime totals keep using its last counters, since they cannot have moved
    while it was not reporting.
    """
    cloud.respond("GET", BATTERIES, json=load_fixture("batteries.json")[:1])
    await refresh(hass, init_integration.runtime_data.telemetry)

    assert state(hass, "sensor", "bat02_power").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", "bat02_charge_energy").state == STATE_UNAVAILABLE
    assert value(hass, "bat01_power") == 1500
    assert value(hass, f"{HUB_ID}_battery_cumulative_charge_energy") == pytest.approx(1422.691)
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(256.737)
    assert state(hass, "sensor", f"{HUB_ID}_average_soc").state == "75"


async def test_failed_battery_read(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """When the battery read fails, energy totals pause rather than guess."""
    cloud.respond("GET", BATTERIES, status=HTTPStatus.REQUEST_TIMEOUT)
    await refresh(hass, init_integration.runtime_data.telemetry)

    assert state(hass, "sensor", "bat01_power").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", f"{HUB_ID}_house_energy").state == STATE_UNAVAILABLE
    assert (
        state(hass, "sensor", f"{HUB_ID}_battery_cumulative_charge_energy").state
        == STATE_UNAVAILABLE
    )
    assert value(hass, "grid1_power") == 2000


async def test_all_telemetry_failing(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """If neither read succeeds every reading is unavailable."""
    cloud.respond("GET", BATTERIES, status=HTTPStatus.BAD_GATEWAY)
    cloud.respond("GET", METERS, status=HTTPStatus.BAD_GATEWAY)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert not init_integration.runtime_data.telemetry.last_update_success
    assert state(hass, "sensor", "grid1_power").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", f"{HUB_ID}_house_power").state == STATE_UNAVAILABLE


async def test_house_energy_not_seeded_from_partial_data(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Starting while batteries cannot be read must not publish a low total."""
    cloud.respond("GET", BATTERIES, status=HTTPStatus.REQUEST_TIMEOUT)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, "sensor", f"{HUB_ID}_house_energy").state == STATE_UNAVAILABLE

    cloud.respond("GET", BATTERIES, json=load_fixture("batteries.json"))
    await refresh(hass, mock_config_entry.runtime_data.telemetry)
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(256.737)


async def test_house_energy_never_decreases(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A lower calculation holds the previous value."""
    batteries = load_fixture("batteries.json")
    batteries[0]["importEnergy"]["value"] = 20_000_000
    cloud.respond("GET", BATTERIES, json=batteries)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(256.737)

    meters = load_fixture("meters.json")
    meters[0]["importEnergy"]["value"] = 50_000_000
    cloud.respond("GET", METERS, json=meters)
    await refresh(hass, init_integration.runtime_data.telemetry)
    # (5000 - 30) + 79 + (730.428 - 2000) + (400 - 500)
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(3679.428)


async def test_energy_totals_hold_across_restart(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A restart does not let an energy total step backwards."""
    mock_config_entry.add_to_hass(hass)
    entity_id = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{HUB_ID}_house_energy",
        suggested_object_id="anode_hub_ehxbt_house_energy",
        config_entry=mock_config_entry,
    ).entity_id
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "300.0"),
                {"native_value": 300.0, "native_unit_of_measurement": "kWh"},
            )
        ],
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == 300.0


@pytest.mark.usefixtures("frozen_time")
async def test_mode_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Mode sensors show the running mode and the next scheduled change."""
    assert state(hass, "sensor", f"{HUB_ID}_mode").state == "CHARGE"
    assert state(hass, "sensor", f"{HUB_ID}_next_mode").state == "DISCHARGE"
    # 16:00 in London (BST) is 15:00 UTC.
    assert state(hass, "sensor", f"{HUB_ID}_next_mode_time").state == "2026-09-15T15:00:00+00:00"


async def test_mode_refreshes_at_schedule_change(
    hass: HomeAssistant,
    frozen_time: datetime,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """The mode is re-read just after a scheduled change, not minutes later."""
    cloud.respond("GET", MODE, json={"mode": "DISCHARGE"})
    freezer.move_to(datetime.fromisoformat("2026-09-15T15:00:06+00:00"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert state(hass, "sensor", f"{HUB_ID}_mode").state == "DISCHARGE"
    assert state(hass, "sensor", f"{HUB_ID}_next_mode").state == "MATCH"
    assert state(hass, "sensor", f"{HUB_ID}_next_mode_time").state == "2026-09-15T18:00:00+00:00"


async def test_device_leaving_status(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Status sensors of a device the hub stops listing become unavailable."""
    status = load_fixture("status.json")
    status["meter"] = [m for m in status["meter"] if m["id"] != "ev001"]
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)

    assert state(hass, "sensor", "ev001_version").state == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor", "ev001_online").state == "off"


async def test_default_polling_intervals(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Each coordinator polls at its documented default interval."""
    runtime = init_integration.runtime_data
    assert runtime.status.update_interval == timedelta(minutes=2)
    assert runtime.telemetry.update_interval == timedelta(seconds=30)
    assert runtime.mode.update_interval == timedelta(minutes=5)
    assert runtime.settings.update_interval == timedelta(minutes=10)


async def test_readings_poll_every_30_seconds(
    hass: HomeAssistant,
    frozen_time: datetime,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """Battery and meter readings are fetched again once 30 seconds pass.

    Time is frozen before setup (via frozen_time) so the coordinator's timer
    is scheduled on the same clock the test advances.
    """
    batteries_before = len(cloud.calls("GET", BATTERIES))
    meters_before = len(cloud.calls("GET", METERS))

    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(cloud.calls("GET", BATTERIES)) == batteries_before

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(cloud.calls("GET", BATTERIES)) == batteries_before + 1
    assert len(cloud.calls("GET", METERS)) == meters_before + 1

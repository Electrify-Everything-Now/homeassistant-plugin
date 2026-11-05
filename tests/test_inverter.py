"""Tests for inverter sensors."""
from __future__ import annotations

from typing import Any

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.const import DOMAIN

from .common import INVERTERS, STATUS, AnodeCloud, load_fixture, refresh, state

PV_KEYS = {"pv_power", "pv_energy"}
BATTERY_KEYS = {
    "battery_power",
    "battery_soc",
    "battery_energy_capacity",
    "battery_charge_energy",
    "battery_discharge_energy",
}
COMMON_KEYS = {
    "state",
    "ac_power",
    "ac_import_energy",
    "ac_export_energy",
    "temperature",
    "fault_code",
    "version",
    "uptime",
}


def value(hass: HomeAssistant, unique_id: str) -> float:
    return float(state(hass, "sensor", unique_id).state)


def inverter_keys(hass: HomeAssistant, entry: MockConfigEntry, inverter_id: str) -> set[str]:
    """The keys of every sensor registered for one inverter."""
    prefix = f"{inverter_id}_"
    return {
        registry_entry.unique_id.removeprefix(prefix)
        for registry_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if registry_entry.domain == "sensor" and registry_entry.unique_id.startswith(prefix)
    }


async def setup_with(
    hass: HomeAssistant,
    cloud: AnodeCloud,
    entry: MockConfigEntry,
    inverters: list[dict[str, Any]],
    readings: list[dict[str, Any]],
) -> None:
    """Set up against a status and inverter read listing these inverters."""
    status = load_fixture("status.json")
    status["inverter"] = inverters
    cloud.respond("GET", STATUS, json=status)
    cloud.respond("GET", INVERTERS, json={"status": True, "inverter": readings})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_hybrid_inverter_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A PV and battery inverter reports in the units the hub sends, normalised."""
    assert state(hass, "sensor", "inv01_state").state == "generating"
    assert value(hass, "inv01_ac_power") == -800
    assert value(hass, "inv01_ac_import_energy") == pytest.approx(12)
    assert value(hass, "inv01_ac_export_energy") == pytest.approx(250)
    assert value(hass, "inv01_temperature") == pytest.approx(34.1)
    assert value(hass, "inv01_fault_code") == 0
    assert value(hass, "inv01_pv_power") == 1200
    assert value(hass, "inv01_pv_energy") == pytest.approx(452.63)
    assert value(hass, "inv01_battery_power") == 400
    assert state(hass, "sensor", "inv01_battery_soc").state == "80"
    assert value(hass, "inv01_battery_energy_capacity") == pytest.approx(8857.6)
    assert value(hass, "inv01_battery_charge_energy") == pytest.approx(23.14)
    assert value(hass, "inv01_battery_discharge_energy") == pytest.approx(74.36)
    assert state(hass, "sensor", "inv01_version").state == "v1.0.0"


async def test_pv_energy_suits_the_energy_dashboard(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """PV energy is a lifetime kWh total, as solar production needs."""
    pv = state(hass, "sensor", "inv01_pv_energy")
    assert pv.name == "GivEnergy PV energy"
    assert pv.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert pv.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
    assert pv.attributes["unit_of_measurement"] == "kWh"

    registry = er.async_get(hass)
    fault = registry.async_get(state(hass, "sensor", "inv01_fault_code").entity_id)
    assert fault.entity_category is EntityCategory.DIAGNOSTIC


async def test_entities_follow_capabilities(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """PV and battery sensors exist only for an inverter that says it has them."""
    reading = load_fixture("inverters.json")["inverter"][0]
    entry = load_fixture("status.json")["inverter"][0]
    await setup_with(
        hass,
        cloud,
        mock_config_entry,
        [
            {**entry, "id": "inv02", "capabilities": ["PV"]},
            {**entry, "id": "inv03", "capabilities": ["BATTERY"]},
            {**entry, "id": "inv04", "capabilities": []},
        ],
        [{**reading, "id": device} for device in ("inv02", "inv03", "inv04")],
    )

    assert inverter_keys(hass, mock_config_entry, "inv02") == COMMON_KEYS | PV_KEYS
    assert inverter_keys(hass, mock_config_entry, "inv03") == COMMON_KEYS | BATTERY_KEYS
    assert inverter_keys(hass, mock_config_entry, "inv04") == COMMON_KEYS


async def test_capability_added_later(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A capability the hub reports later adds its sensors without a reload."""
    status = load_fixture("status.json")
    status["inverter"].append({**status["inverter"][0], "id": "inv02", "capabilities": ["PV"]})
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)
    assert inverter_keys(hass, init_integration, "inv02") == COMMON_KEYS | PV_KEYS

    status["inverter"][1]["capabilities"] = ["PV", "BATTERY"]
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)
    assert inverter_keys(hass, init_integration, "inv02") == COMMON_KEYS | PV_KEYS | BATTERY_KEYS


@pytest.mark.parametrize("reported", ["UNKNOWN", "SOME_FUTURE_STATE"])
async def test_state_not_known(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud, reported: str
) -> None:
    """A state the inverter does not know, or this integration does not, reads as unknown."""
    readings = load_fixture("inverters.json")
    readings["inverter"][0]["detailed"]["state"] = reported
    cloud.respond("GET", INVERTERS, json=readings)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert state(hass, "sensor", "inv01_state").state == STATE_UNKNOWN


async def test_comms_lost(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """An inverter the bridge cannot reach reports zero power and keeps its counters."""
    readings = load_fixture("inverters.json")
    detailed = readings["inverter"][0]["detailed"]
    detailed["state"] = "COMMS_LOST"
    for block in ("ac", "pv", "battery"):
        detailed[block]["power"]["value"] = 0
    cloud.respond("GET", INVERTERS, json=readings)
    await refresh(hass, init_integration.runtime_data.telemetry)

    assert state(hass, "sensor", "inv01_state").state == "comms_lost"
    assert value(hass, "inv01_ac_power") == 0
    assert value(hass, "inv01_ac_export_energy") == pytest.approx(250)


async def test_inverter_dropout(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """An inverter missing from a read is unavailable, never zero."""
    cloud.respond("GET", INVERTERS, json={"status": True, "inverter": []})
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert state(hass, "sensor", "inv01_ac_power").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", "inv01_battery_soc").state == STATE_UNAVAILABLE
    # Status entities come from hub status, not telemetry.
    assert state(hass, "sensor", "inv01_version").state == "v1.0.0"
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "inv01_pv_power")

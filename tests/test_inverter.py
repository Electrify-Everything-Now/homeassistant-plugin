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

from .common import (
    BATTERIES,
    HUB_ID,
    INVERTERS,
    METERS,
    STATUS,
    AnodeCloud,
    load_fixture,
    refresh,
    state,
)

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


def inverter_reading(**changes: Any) -> dict[str, Any]:
    """The fixture inverter read, with AC or battery power changed."""
    readings = load_fixture("inverters.json")
    detailed = readings["inverter"][0]["detailed"]
    for block, watts in changes.items():
        detailed[block]["power"]["value"] = watts
    return readings


async def test_givenergy_supplying_the_house(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """With a grid meter and a hybrid inverter only, what the inverter supplies is house load."""
    status = load_fixture("status.json")
    status["battery"] = []
    status["meter"] = [m for m in status["meter"] if m["id"] == "grid1"]
    cloud.respond("GET", STATUS, json=status)
    cloud.respond("GET", BATTERIES, json=[])
    meters = [m for m in load_fixture("meters.json") if m["id"] == "grid1"]
    meters[0]["power"]["value"] = 200
    cloud.respond("GET", METERS, json=meters)
    cloud.respond("GET", INVERTERS, json=inverter_reading(ac=-1800))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 200 from the grid and 1800 from the inverter.
    assert value(hass, f"{HUB_ID}_house_power") == 2000
    # (500 - 30) grid + (250 - 12) inverter
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(708)
    assert value(hass, f"{HUB_ID}_battery_energy_capacity") == pytest.approx(8857.6)
    assert state(hass, "sensor", f"{HUB_ID}_average_soc").state == "80"

    readings = inverter_reading(ac=-1800)
    readings["inverter"][0]["energy"]["export"]["value"] += 50_000
    cloud.respond("GET", INVERTERS, json=readings)
    await refresh(hass, mock_config_entry.runtime_data.telemetry)
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(713)


async def test_inverter_battery_is_inside_its_ac_port(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """House power follows the AC port; the battery behind it is not counted again."""
    cloud.respond("GET", INVERTERS, json=inverter_reading(ac=-800, battery=-2000))
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert value(hass, f"{HUB_ID}_house_power") == 4300

    cloud.respond("GET", INVERTERS, json=inverter_reading(ac=300))
    await refresh(hass, init_integration.runtime_data.telemetry)
    # The inverter now draws 300 W, charging its battery from the grid.
    assert value(hass, f"{HUB_ID}_house_power") == 3200


async def test_house_unknown_without_the_inverter(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """House figures pause while the inverter's part of them is unknown, rather than guess."""
    telemetry = init_integration.runtime_data.telemetry
    cloud.respond("GET", INVERTERS, json={"status": False, "info": "No inverter data yet"})
    await refresh(hass, telemetry)
    assert state(hass, "sensor", f"{HUB_ID}_house_power").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", f"{HUB_ID}_house_energy").state == STATE_UNAVAILABLE

    readings = load_fixture("inverters.json")
    del readings["inverter"][0]["energy"]
    cloud.respond("GET", INVERTERS, json=readings)
    await refresh(hass, telemetry)
    assert value(hass, f"{HUB_ID}_house_power") == 4300
    assert state(hass, "sensor", f"{HUB_ID}_house_energy").state == STATE_UNAVAILABLE

    readings = load_fixture("inverters.json")
    del readings["inverter"][0]["detailed"]["ac"]
    cloud.respond("GET", INVERTERS, json=readings)
    await refresh(hass, telemetry)
    assert state(hass, "sensor", f"{HUB_ID}_house_power").state == STATE_UNAVAILABLE
    assert value(hass, f"{HUB_ID}_house_energy") == pytest.approx(494.737)


async def test_house_energy_needs_every_inverter(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """An inverter that has never reported makes house energy unknown."""
    status = load_fixture("status.json")
    status["inverter"].append({**status["inverter"][0], "id": "inv02"})
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert state(hass, "sensor", f"{HUB_ID}_house_energy").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", f"{HUB_ID}_house_power").state == STATE_UNAVAILABLE


async def test_soc_counts_inverter_batteries_of_known_capacity(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """The inverter battery weighs into the average by capacity, and only once it is known."""
    # (4528.8 + 2400 + 7086.08) / (6038.4 + 4800 + 8857.6)
    assert state(hass, "sensor", f"{HUB_ID}_average_soc").state == "71"

    readings = load_fixture("inverters.json")
    readings["inverter"][0]["detailed"]["battery"]["capacity"]["value"] = 0
    cloud.respond("GET", INVERTERS, json=readings)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert state(hass, "sensor", f"{HUB_ID}_average_soc").state == "64"
    assert value(hass, f"{HUB_ID}_battery_energy_capacity") == 10838.4
    assert state(hass, "sensor", "inv01_battery_energy_capacity").state == STATE_UNKNOWN


async def test_soc_ignores_a_battery_the_inverter_does_not_claim(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Battery readings from an inverter without the BATTERY capability are left out."""
    status = load_fixture("status.json")
    status["inverter"][0]["capabilities"] = ["PV"]
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert state(hass, "sensor", f"{HUB_ID}_average_soc").state == "64"
    assert value(hass, f"{HUB_ID}_battery_energy_remaining") == 6928.8

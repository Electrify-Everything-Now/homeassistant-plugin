"""Tests for the hub lifetime energy totals and what they carry across."""
from __future__ import annotations

from copy import deepcopy
from http import HTTPStatus
from typing import Any

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
from custom_components.anode_battery.energy import SAVE_DELAY, STORAGE_VERSION

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

HOUSE = f"{HUB_ID}_house_energy"
HOUSE_TODAY = f"{HUB_ID}_house_energy_today"
GRID_IMPORT = f"{HUB_ID}_grid_import_energy"
CHARGE = f"{HUB_ID}_battery_cumulative_charge_energy"
DISCHARGE = f"{HUB_ID}_battery_cumulative_discharge_energy"
# What the fixtures add up to before anything moves.
HOUSE_START = 256.737
CHARGE_START = 1422.691
DISCHARGE_START = 1130.428

pytestmark = pytest.mark.usefixtures("frozen_time")


def value(hass: HomeAssistant, unique_id: str) -> float:
    return float(state(hass, "sensor", unique_id).state)


def storage_key(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}.energy_totals.{entry.entry_id}"


def meters(**import_kwh: float) -> list[dict[str, Any]]:
    """The meter read, with import counters set in kWh."""
    data = load_fixture("meters.json")
    for meter in data:
        if meter["id"] in import_kwh:
            meter["importEnergy"]["value"] = round(import_kwh[meter["id"]] * 10000)
    return data


def inverters(export_kwh: float) -> dict[str, Any]:
    """The inverter read, with the AC export counter set in kWh."""
    data = load_fixture("inverters.json")
    data["inverter"][0]["energy"]["export"]["value"] = round(export_kwh * 10000)
    return data


async def test_fresh_install_starts_from_the_counters(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Totals start at the sum of meter and battery counters, and are saved."""
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START)
    assert value(hass, GRID_IMPORT) == pytest.approx(500)
    assert value(hass, CHARGE) == pytest.approx(CHARGE_START)
    assert value(hass, HOUSE_TODAY) == 0

    freezer.tick(SAVE_DELAY)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    stored = hass_storage[storage_key(init_integration)]["data"]
    assert stored["totals"]["house_energy"] == pytest.approx(HOUSE_START)
    assert stored["counters"]["house_energy"]["inv01:export_energy_kwh"] == pytest.approx(250)
    assert stored["counters"]["grid_import_energy"] == {"grid1:import_energy_kwh": 500}


async def test_upgrade_with_an_inverter_does_not_step(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """An install that showed totals without the inverter carries on from them."""
    mock_config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    lifetime, today = (
        registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=mock_config_entry
        ).entity_id
        for unique_id in (HOUSE, HOUSE_TODAY)
    )
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(lifetime, str(HOUSE_START)),
                {"native_value": HOUSE_START, "native_unit_of_measurement": "kWh"},
            ),
            (
                State(today, "6.737"),
                {
                    "today": 6.737,
                    "baseline": 250.0,
                    "last_total": HOUSE_START,
                    "day": "2026-09-15",
                },
            ),
        ],
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START)
    assert value(hass, HOUSE_TODAY) == pytest.approx(6.737)

    cloud.respond("GET", INVERTERS, json=inverters(252))
    await refresh(hass, mock_config_entry.runtime_data.telemetry)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 2)
    assert value(hass, HOUSE_TODAY) == pytest.approx(8.737)


async def test_pairing_a_device_does_not_step(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A battery or generation meter paired mid-day counts from its first reading."""
    status = load_fixture("status.json")
    status["battery"].append({**status["battery"][1], "id": "bat03"})
    status["meter"].append({**status["meter"][1], "id": "solar2"})
    battery_read = load_fixture("batteries.json")
    battery_read.append({**deepcopy(battery_read[1]), "id": "bat03"})
    meter_read = load_fixture("meters.json")
    meter_read.append({**deepcopy(meter_read[1]), "id": "solar2"})
    cloud.respond("GET", STATUS, json=status)
    cloud.respond("GET", BATTERIES, json=battery_read)
    cloud.respond("GET", METERS, json=meter_read)
    runtime = init_integration.runtime_data
    await refresh(hass, runtime.status)
    await refresh(hass, runtime.telemetry)

    assert value(hass, HOUSE) == pytest.approx(HOUSE_START)
    assert value(hass, CHARGE) == pytest.approx(CHARGE_START)
    assert value(hass, HOUSE_TODAY) == 0

    battery_read[2]["exportEnergy"]["value"] += 10000
    cloud.respond("GET", BATTERIES, json=battery_read)
    await refresh(hass, runtime.telemetry)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 1)
    assert value(hass, DISCHARGE) == pytest.approx(DISCHARGE_START + 1)
    assert value(hass, HOUSE_TODAY) == pytest.approx(1)


async def test_removing_a_device_does_not_drop(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A device the hub stops listing stops counting; the totals keep what it gave."""
    status = load_fixture("status.json")
    status["battery"] = status["battery"][:1]
    status["meter"] = [meter for meter in status["meter"] if meter["id"] != "solar1"]
    cloud.respond("GET", STATUS, json=status)
    runtime = init_integration.runtime_data
    await refresh(hass, runtime.status)
    await refresh(hass, runtime.telemetry)

    assert value(hass, HOUSE) == pytest.approx(HOUSE_START)
    assert value(hass, CHARGE) == pytest.approx(CHARGE_START)
    assert value(hass, HOUSE_TODAY) == 0

    cloud.respond("GET", METERS, json=meters(grid1=501))
    await refresh(hass, runtime.telemetry)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 1)


async def test_counter_reset_keeps_counting(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A counter that falls counts on from its new value."""
    telemetry = init_integration.runtime_data.telemetry
    cloud.respond("GET", METERS, json=meters(grid1=0))
    await refresh(hass, telemetry)
    assert value(hass, GRID_IMPORT) == pytest.approx(500)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START)

    cloud.respond("GET", METERS, json=meters(grid1=2))
    await refresh(hass, telemetry)
    assert value(hass, GRID_IMPORT) == pytest.approx(502)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 2)
    assert value(hass, HOUSE_TODAY) == pytest.approx(2)


async def test_restart_counts_the_downtime(
    hass: HomeAssistant,
    cloud: AnodeCloud,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Stored totals carry on, counting what moved while Home Assistant was stopped."""
    hass_storage[storage_key(mock_config_entry)] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": storage_key(mock_config_entry),
        "data": {
            "totals": {"house_energy": 1000.0, "grid_import_energy": 600.0},
            "counters": {
                "house_energy": {
                    "grid1:import_energy_kwh": 495.0,
                    "grid1:export_energy_kwh": 30.0,
                    "inv01:export_energy_kwh": 248.0,
                },
                "grid_import_energy": {"grid1:import_energy_kwh": 495.0},
            },
        },
    }
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # 5 kWh imported and 2 kWh from the inverter while stopped.
    assert value(hass, HOUSE) == pytest.approx(1007)
    assert value(hass, GRID_IMPORT) == pytest.approx(605)
    # A total the store does not hold starts from the counters.
    assert value(hass, f"{HUB_ID}_grid_export_energy") == pytest.approx(30)


async def test_failed_and_stale_reads_catch_up(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A read that cannot vouch for a total leaves it; the next good read counts the gap."""
    telemetry = init_integration.runtime_data.telemetry
    cloud.respond("GET", METERS, status=HTTPStatus.REQUEST_TIMEOUT)
    await refresh(hass, telemetry)
    assert state(hass, "sensor", GRID_IMPORT).state == STATE_UNAVAILABLE
    assert state(hass, "sensor", HOUSE).state == STATE_UNAVAILABLE

    cloud.respond("GET", METERS, json=meters(grid1=503))
    await refresh(hass, telemetry)
    assert value(hass, GRID_IMPORT) == pytest.approx(503)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 3)

    cloud.respond("GET", INVERTERS, json={"status": False, "info": "No inverter data yet"})
    await refresh(hass, telemetry)
    assert state(hass, "sensor", HOUSE).state == STATE_UNAVAILABLE
    cloud.respond("GET", INVERTERS, json=inverters(254))
    await refresh(hass, telemetry)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 7)

    # bat02 drops out of a read: its last counters stand in, so nothing moves.
    battery_read = load_fixture("batteries.json")
    cloud.respond("GET", BATTERIES, json=battery_read[:1])
    await refresh(hass, telemetry)
    assert value(hass, DISCHARGE) == pytest.approx(DISCHARGE_START)

    battery_read[1]["exportEnergy"]["value"] += 20000
    cloud.respond("GET", BATTERIES, json=battery_read)
    await refresh(hass, telemetry)
    assert value(hass, DISCHARGE) == pytest.approx(DISCHARGE_START + 2)
    assert value(hass, HOUSE) == pytest.approx(HOUSE_START + 9)
    assert value(hass, HOUSE_TODAY) == pytest.approx(9)


async def test_removing_the_entry_deletes_its_totals(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Unloading writes the totals at once, and removing the entry deletes them."""
    assert storage_key(init_integration) not in hass_storage
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert storage_key(init_integration) in hass_storage

    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    assert storage_key(init_integration) not in hass_storage

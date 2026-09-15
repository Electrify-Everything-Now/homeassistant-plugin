"""Tests for battery BMS readings: pack voltage, temperatures and cells."""
from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.api import AnodeClient, AnodeCommandError
from custom_components.anode_battery.const import DOMAIN

from .common import API_KEY, BATTERIES, EMAIL, HUB_ID, AnodeCloud, load_fixture, refresh, state

BAT01 = {"id": "bat01"}
BAT02 = {"id": "bat02"}


def make_client(hass: HomeAssistant) -> AnodeClient:
    return AnodeClient(async_get_clientsession(hass), EMAIL, API_KEY)


def registry_id(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


def single_reads(cloud: AnodeCloud, query: dict[str, str]) -> int:
    return len(cloud.calls("GET", BATTERIES, query=query))


async def enable(hass: HomeAssistant, entry: MockConfigEntry, *unique_ids: str) -> list[str]:
    """Enable disabled-by-default entities and reload so they are added."""
    registry = er.async_get(hass)
    entity_ids = [registry_id(hass, unique_id) for unique_id in unique_ids]
    for entity_id in entity_ids:
        registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entity_ids


async def test_single_battery_read_includes_bms(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """BMS values are parsed and cell millivolts become volts."""
    client = make_client(hass)
    bat01 = await client.get_battery(HUB_ID, "bat01")
    assert bat01.power_w == 1500
    assert bat01.pack_voltage_v == pytest.approx(46.101)
    assert bat01.temperatures_c == (23.5, 23.7)
    assert bat01.max_temperature_c == 23.7
    assert bat01.min_temperature_c == 23.5
    assert len(bat01.cell_voltages_v) == 12
    assert bat01.cell_voltages_v[0] == pytest.approx(3.842)
    assert bat01.max_cell_voltage_v == pytest.approx(3.848)
    assert bat01.min_cell_voltage_v == pytest.approx(3.842)
    assert bat01.cell_voltage_difference_mv == pytest.approx(6)

    # Older battery firmware sends no BMS block.
    bat02 = await client.get_battery(HUB_ID, "bat02")
    assert bat02.bms is None
    assert (bat02.pack_voltage_v, bat02.temperatures_c, bat02.cell_voltages_v) == (None, (), ())
    assert bat02.cell_voltage_difference_mv is None


async def test_single_battery_unknown_id(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """The hub's unknown-id answer raises."""
    cloud.respond(
        "GET", BATTERIES, query={"id": "nope"}, json={"status": False, "info": "Unknown ID"}
    )
    with pytest.raises(AnodeCommandError, match="Unknown ID"):
        await make_client(hass).get_battery(HUB_ID, "nope")


async def test_bms_units_and_unreadable_values(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Other units convert; an unreadable value drops its list, not one position."""
    battery = load_fixture("battery_bat01.json")
    battery["bms"] = {
        "voltageAct": {"value": 46101, "unit": "mV"},
        "ntcTemp": {"values": [74.3, 212], "unit": "°F"},
        "cells": {"values": [3842, None, 3848], "unit": "mV"},
    }
    cloud.respond("GET", BATTERIES, query=BAT01, json=battery)
    bat01 = await make_client(hass).get_battery(HUB_ID, "bat01")

    assert bat01.pack_voltage_v == pytest.approx(46.101)
    assert bat01.temperatures_c == pytest.approx((23.5, 100))
    assert bat01.cell_voltages_v == ()


async def test_bms_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Summary sensors are enabled; detail sensors exist but start disabled."""
    assert float(state(hass, "sensor", "bat01_pack_voltage").state) == pytest.approx(46.101)
    assert float(state(hass, "sensor", "bat01_max_temperature").state) == 23.7
    assert float(state(hass, "sensor", "bat01_cell_voltage_difference").state) == pytest.approx(6)
    assert state(hass, "sensor", "bat01_pack_voltage").name == "Garage battery Pack voltage"
    # Power and energy still come from the all-batteries read.
    assert float(state(hass, "sensor", "bat01_power").state) == 1500

    registry = er.async_get(hass)
    for key in (
        "min_temperature",
        "max_cell_voltage",
        "min_cell_voltage",
        "temperature_1",
        "cell_voltage_1",
        "cell_voltage_12",
    ):
        entry = registry.async_get(registry_id(hass, f"bat01_{key}"))
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # A battery without BMS data gets no BMS entities.
    assert registry_id(hass, "bat02_pack_voltage") is None
    assert registry_id(hass, "bat02_cell_voltage_1") is None


async def test_cell_and_probe_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Enabled per-cell and per-probe sensors are numbered from 1."""
    cell, probe, lowest = await enable(
        hass,
        init_integration,
        "bat01_cell_voltage_1",
        "bat01_temperature_2",
        "bat01_min_cell_voltage",
    )
    assert float(hass.states.get(cell).state) == pytest.approx(3.842)
    assert hass.states.get(cell).name == "Garage battery Cell 1 voltage"
    assert float(hass.states.get(probe).state) == 23.7
    assert hass.states.get(probe).name == "Garage battery Temperature 2"
    assert float(hass.states.get(lowest).state) == pytest.approx(3.842)


@pytest.mark.usefixtures("frozen_time")
async def test_bms_read_less_often_than_power(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """Each battery is read individually every 2 minutes, not on every poll."""
    telemetry = init_integration.runtime_data.telemetry
    assert single_reads(cloud, BAT01) == 1

    await refresh(hass, telemetry)
    assert single_reads(cloud, BAT01) == 1

    freezer.tick(timedelta(minutes=2))
    await refresh(hass, telemetry)
    assert single_reads(cloud, BAT01) == 2
    # bat02's firmware reports no BMS data, so it is only re-checked hourly.
    assert single_reads(cloud, BAT02) == 1


@pytest.mark.usefixtures("frozen_time")
async def test_bms_sensors_appear_after_firmware_update(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """A battery that starts reporting BMS data gets its sensors within the hour."""
    battery = load_fixture("battery_bat02.json")
    battery["bms"] = load_fixture("battery_bat01.json")["bms"]
    cloud.respond("GET", BATTERIES, query=BAT02, json=battery)
    telemetry = init_integration.runtime_data.telemetry

    freezer.tick(timedelta(minutes=30))
    await refresh(hass, telemetry)
    assert registry_id(hass, "bat02_pack_voltage") is None

    freezer.tick(timedelta(minutes=31))
    await refresh(hass, telemetry)
    assert float(state(hass, "sensor", "bat02_pack_voltage").state) == pytest.approx(46.101)
    assert registry_id(hass, "bat02_cell_voltage_12") is not None


async def test_bms_in_all_batteries_read_skips_single_reads(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Once firmware includes BMS data in the all-batteries read, no extra requests."""
    batteries = load_fixture("batteries.json")
    batteries[0]["bms"] = load_fixture("battery_bat01.json")["bms"]
    cloud.respond("GET", BATTERIES, json=batteries)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert single_reads(cloud, BAT01) == 0
    assert float(state(hass, "sensor", "bat01_pack_voltage").state) == pytest.approx(46.101)


@pytest.mark.usefixtures("frozen_time")
async def test_failed_bms_read_keeps_last_values(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """A failed single-battery read keeps BMS values and doesn't affect power."""
    cloud.respond("GET", BATTERIES, query=BAT01, status=HTTPStatus.REQUEST_TIMEOUT)
    freezer.tick(timedelta(minutes=2))
    await refresh(hass, init_integration.runtime_data.telemetry)

    assert single_reads(cloud, BAT01) == 2
    assert float(state(hass, "sensor", "bat01_pack_voltage").state) == pytest.approx(46.101)
    assert float(state(hass, "sensor", "bat01_power").state) == 1500


@pytest.mark.usefixtures("frozen_time")
async def test_bms_auth_failure_starts_reauth(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """A rejected key on a single-battery read asks the user to re-authenticate."""
    cloud.respond("GET", BATTERIES, query=BAT01, status=HTTPStatus.UNAUTHORIZED)
    freezer.tick(timedelta(minutes=2))
    await refresh(hass, init_integration.runtime_data.telemetry)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.usefixtures("frozen_time")
async def test_cell_no_longer_reported(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """A cell missing from a reading is unavailable rather than a stale value."""
    (cell_12,) = await enable(hass, init_integration, "bat01_cell_voltage_12")
    assert float(hass.states.get(cell_12).state) == pytest.approx(3.842)

    battery = load_fixture("battery_bat01.json")
    battery["bms"]["cells"]["values"] = battery["bms"]["cells"]["values"][:11]
    cloud.respond("GET", BATTERIES, query=BAT01, json=battery)
    freezer.tick(timedelta(minutes=2))
    await refresh(hass, init_integration.runtime_data.telemetry)
    assert hass.states.get(cell_12).state == STATE_UNAVAILABLE

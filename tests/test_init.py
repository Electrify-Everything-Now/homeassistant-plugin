"""Tests for Anode setup, devices and migration."""
from __future__ import annotations

from http import HTTPStatus

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery import async_remove_config_entry_device
from custom_components.anode_battery.const import DOMAIN

from .common import BATTERIES, HUB_ID, METADATA, STATUS, AnodeCloud, load_fixture, refresh, state

# Every unique id the fixture system should produce. Unique ids are the
# contract with existing installs: changing one orphans history.
EXPECTED_UNIQUE_IDS = {
    "sensor": {
        *(
            f"{HUB_ID}_{key}"
            for key in (
                "version",
                "uptime",
                "mode",
                "next_mode",
                "next_mode_time",
                "battery_energy_capacity",
                "battery_energy_remaining",
                "average_soc",
                "battery_cumulative_charge_energy",
                "battery_cumulative_discharge_energy",
                "grid_import_energy",
                "grid_export_energy",
                "house_power",
                "house_energy",
                "battery_charge_energy_today",
                "battery_discharge_energy_today",
                "grid_import_energy_today",
                "grid_export_energy_today",
                "house_energy_today",
            )
        ),
        *(
            f"{battery}_{key}"
            for battery in ("bat01", "bat02")
            for key in (
                "power",
                "import_power",
                "export_power",
                "soc",
                "capacity",
                "capacity_remaining",
                "energy_capacity",
                "energy_remaining",
                "nominal_voltage",
                "power_status",
                "charge_energy",
                "discharge_energy",
                "version",
                "uptime",
            )
        ),
        # BMS sensors: only bat01's firmware reports BMS data in the fixtures.
        *(
            f"bat01_{key}"
            for key in (
                "pack_voltage",
                "max_temperature",
                "min_temperature",
                "cell_voltage_difference",
                "max_cell_voltage",
                "min_cell_voltage",
                "temperature_1",
                "temperature_2",
                *(f"cell_voltage_{cell}" for cell in range(1, 13)),
            )
        ),
        *(
            f"{meter}_{key}"
            for meter in ("grid1", "solar1", "ev001")
            for key in (
                "power",
                "import_power",
                "export_power",
                "import_energy",
                "export_energy",
                "voltage",
                "current",
                "power_factor",
                "type",
                "version",
                "uptime",
            )
        ),
        "ev001_parent_meter",
    },
    "binary_sensor": {
        f"{HUB_ID}_online",
        f"{HUB_ID}_override_active",
        *(f"{device}_online" for device in ("bat01", "bat02", "grid1", "solar1", "ev001")),
    },
    "number": {
        f"{HUB_ID}_override_duration",
        f"{HUB_ID}_max_charge_power",
        f"{HUB_ID}_max_discharge_power",
        f"{HUB_ID}_max_charge_power_percent",
        f"{HUB_ID}_max_discharge_power_percent",
        *(f"{battery}_{bound}_soc" for battery in ("bat01", "bat02") for bound in ("min", "max")),
    },
    "select": {f"{HUB_ID}_override_mode"},
    "button": {f"{HUB_ID}_cancel_override"},
}


def legacy_entry(minor_version: int = 1) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=HUB_ID,
        version=1,
        minor_version=minor_version,
        data={"email": "owner@example.com", "api_key": "k", "hub_id": HUB_ID},
    )


async def test_setup_and_unload(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The entry loads and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_unique_ids(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Entities keep the unique ids earlier releases used."""
    entries = er.async_entries_for_config_entry(er.async_get(hass), init_integration.entry_id)
    found: dict[str, set[str]] = {}
    for entry in entries:
        found.setdefault(entry.domain, set()).add(entry.unique_id)
    assert found == EXPECTED_UNIQUE_IDS


async def test_setup_retries_when_hub_offline(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """An unreachable hub makes setup retry later."""
    cloud.respond("GET", STATUS, status=HTTPStatus.REQUEST_TIMEOUT)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected API key asks the user to re-authenticate."""
    cloud.respond("GET", STATUS, status=HTTPStatus.UNAUTHORIZED)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_devices(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Devices are named from web-app aliases and linked to the hub."""
    registry = dr.async_get(hass)
    hub = registry.async_get_device({(DOMAIN, HUB_ID)})
    assert hub is not None
    assert (hub.name, hub.model, hub.sw_version) == ("Home hub", "Hub", "2.4.1")

    expected = {
        "bat01": ("Garage battery", "Battery"),
        "bat02": ("Anode Battery bat02", "Battery"),
        "grid1": ("Grid", "Meter"),
        "solar1": ("Solar", "Meter"),
        "ev001": ("Anode Meter ev001", "Meter"),
    }
    for device_id, (name, model) in expected.items():
        device = registry.async_get_device({(DOMAIN, device_id)})
        assert device is not None
        assert (device.name, device.model, device.via_device_id) == (name, model, hub.id)


async def test_alias_changes_keep_user_renames(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Web-app renames update devices; a rename made in HA still wins."""
    registry = dr.async_get(hass)
    battery = registry.async_get_device({(DOMAIN, "bat01")})
    registry.async_update_device(battery.id, name_by_user="My name")

    metadata = load_fixture("device_metadata.json")
    for item in metadata["metadata"]:
        if item["friendlyId"] in ("bat01", "bat02"):
            item["alias"] = f"Renamed {item['friendlyId']}"
    cloud.respond("GET", METADATA, json=metadata)
    await refresh(hass, init_integration.runtime_data.status)

    battery = registry.async_get_device({(DOMAIN, "bat01")})
    assert (battery.name, battery.name_by_user) == ("Renamed bat01", "My name")
    assert registry.async_get_device({(DOMAIN, "bat02")}).name == "Renamed bat02"


async def test_new_battery_appears(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A battery paired after setup gets a device and entities without a reload."""
    status = load_fixture("status.json")
    status["battery"].append({"id": "bat03", "version": "1.3.0", "uptime": 1000, "online": True})
    batteries = load_fixture("batteries.json")
    batteries.append({**batteries[1], "id": "bat03", "soc": {"value": 33, "unit": "%"}})
    cloud.respond("GET", STATUS, json=status)
    cloud.respond("GET", BATTERIES, json=batteries)

    runtime = init_integration.runtime_data
    await refresh(hass, runtime.status)
    assert dr.async_get(hass).async_get_device({(DOMAIN, "bat03")}) is not None
    await refresh(hass, runtime.telemetry)
    assert state(hass, "sensor", "bat03_soc").state == "33"
    assert state(hass, "number", "bat03_min_soc") is not None


async def test_stale_devices_can_be_removed(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Only devices the hub no longer reports can be deleted."""
    registry = dr.async_get(hass)
    current = registry.async_get_device({(DOMAIN, "bat01")})
    stale = registry.async_get_or_create(
        config_entry_id=init_integration.entry_id, identifiers={(DOMAIN, "oldbat")}
    )
    assert not await async_remove_config_entry_device(hass, init_integration, current)
    assert await async_remove_config_entry_device(hass, init_integration, stale)


async def test_migration_removes_retired_entities(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Upgrading from 1.1 removes retired entities, keeps the rest, and explains."""
    entry = legacy_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for platform, key in (
        ("sensor", "house_energy_today"),
        ("select", "charge_override"),
        ("sensor", "grid_import_energy"),
        ("sensor", "house_energy"),
    ):
        registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{HUB_ID}_{key}",
            suggested_object_id=f"anode_hub_{HUB_ID}_{key}",
            config_entry=entry,
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert registry.async_get("select.anode_hub_ehxbt_charge_override") is None
    # Kept entities keep their original entity ids.
    for kept in ("house_energy_today", "grid_import_energy", "house_energy"):
        assert hass.states.get(f"sensor.anode_hub_ehxbt_{kept}") is not None

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"retired_entities_{entry.entry_id}")
    assert issue is not None
    assert (
        issue.translation_placeholders["entities"] == "- `select.anode_hub_ehxbt_charge_override`"
    )


async def test_migration_without_retired_entities(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """No repair issue when there was nothing to remove."""
    entry = legacy_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"retired_entities_{entry.entry_id}") is None


async def test_future_version_is_refused(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """An entry from a newer major version is not loaded."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=HUB_ID, version=2, data={"hub_id": HUB_ID})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_actions_registered(hass: HomeAssistant) -> None:
    """The integration sets up without YAML and registers its actions."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "set_override")
    assert hass.services.has_service(DOMAIN, "cancel_override")

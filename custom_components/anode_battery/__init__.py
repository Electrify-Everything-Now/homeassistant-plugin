"""The Anode integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.const import CONF_API_KEY, CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AnodeClient, HubStatus
from .const import (
    CONF_DEVICE_INTERVAL,
    CONF_HUB_ID,
    CONF_STATUS_INTERVAL,
    DEFAULT_DEVICE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    REMOVED_HUB_SELECT_KEYS,
    REMOVED_HUB_SENSOR_KEYS,
)
from .coordinator import (
    AnodeConfigEntry,
    AnodeModeCoordinator,
    AnodeRuntimeData,
    AnodeSettingsCoordinator,
    AnodeStatusCoordinator,
    AnodeTelemetryCoordinator,
)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AnodeConfigEntry) -> bool:
    """Set up an Anode hub from a config entry."""
    hub_id: str = entry.data[CONF_HUB_ID]
    client = AnodeClient(
        async_get_clientsession(hass), entry.data[CONF_EMAIL], entry.data[CONF_API_KEY]
    )
    status = AnodeStatusCoordinator(
        hass,
        entry,
        client,
        hub_id,
        timedelta(seconds=entry.options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)),
    )
    telemetry = AnodeTelemetryCoordinator(
        hass,
        entry,
        client,
        hub_id,
        timedelta(seconds=entry.options.get(CONF_DEVICE_INTERVAL, DEFAULT_DEVICE_INTERVAL)),
    )
    mode = AnodeModeCoordinator(hass, entry, client, hub_id)
    settings = AnodeSettingsCoordinator(hass, entry, client, hub_id)

    # Status first: it proves the credentials and lists the devices.
    await status.async_config_entry_first_refresh()
    # Settings are not needed to run, so a failed read does not block setup.
    results = await asyncio.gather(
        telemetry.async_config_entry_first_refresh(),
        mode.async_config_entry_first_refresh(),
        settings.async_refresh(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result

    entry.runtime_data = AnodeRuntimeData(
        client=client,
        hub_id=hub_id,
        status=status,
        telemetry=telemetry,
        mode=mode,
        settings=settings,
    )

    # Devices must exist before entities link to them, so this listener is
    # registered before any platform adds its own.
    async_sync_devices(hass, entry, status.data)
    entry.async_on_unload(
        status.async_add_listener(lambda: async_sync_devices(hass, entry, status.data))
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AnodeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: AnodeConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: AnodeConfigEntry) -> bool:
    """Migrate an older config entry."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        _async_remove_retired_entities(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


@callback
def _async_remove_retired_entities(hass: HomeAssistant, entry: AnodeConfigEntry) -> None:
    """Remove entities dropped in 1.2 and tell the user what replaces them."""
    registry = er.async_get(hass)
    hub_id = entry.data[CONF_HUB_ID]
    retired = {("sensor", f"{hub_id}_{key}") for key in REMOVED_HUB_SENSOR_KEYS} | {
        ("select", f"{hub_id}_{key}") for key in REMOVED_HUB_SELECT_KEYS
    }
    removed: list[str] = []
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (registry_entry.domain, registry_entry.unique_id) in retired:
            removed.append(registry_entry.entity_id)
            registry.async_remove(registry_entry.entity_id)
    if not removed:
        return
    _LOGGER.info("Removed retired Anode entities: %s", ", ".join(removed))
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"retired_entities_{entry.entry_id}",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="retired_entities",
        translation_placeholders={
            "entities": "\n".join(f"- `{entity_id}`" for entity_id in sorted(removed))
        },
    )


@callback
def async_sync_devices(hass: HomeAssistant, entry: AnodeConfigEntry, status: HubStatus) -> None:
    """Create or update the hub, battery and meter devices.

    Web-UI aliases are written to ``name``, not ``name_by_user``, so a rename
    made in Home Assistant still wins.
    """
    registry = dr.async_get(hass)

    def upsert(
        identifier: str,
        *,
        name: str,
        model: str,
        sw_version: str | None,
        via_device: tuple[str, str] | None = None,
    ) -> None:
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            model=model,
            name=name,
            serial_number=identifier,
            via_device=via_device,
        )
        changes: dict[str, str] = {}
        if device.name != name:
            changes["name"] = name
        if sw_version and device.sw_version != sw_version:
            changes["sw_version"] = sw_version
        if changes:
            registry.async_update_device(device.id, **changes)

    hub = (DOMAIN, status.hub_id)
    upsert(
        status.hub_id,
        name=status.alias or f"Anode Hub {status.hub_id}",
        model="Hub",
        sw_version=status.version,
    )
    for battery in status.batteries.values():
        upsert(
            battery.id,
            name=battery.alias or f"Anode Battery {battery.id}",
            model="Battery",
            sw_version=battery.version,
            via_device=hub,
        )
    for meter in status.meters.values():
        upsert(
            meter.id,
            name=meter.alias or f"Anode Meter {meter.id}",
            model="Meter",
            sw_version=meter.version,
            via_device=hub,
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AnodeConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device the hub no longer reports."""
    status = entry.runtime_data.status.data
    current = {status.hub_id, *status.batteries, *status.meters}
    return not any(
        identifier in current
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    )

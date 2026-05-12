"""The Anode integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_HUB_ID,
    CONF_STATUS_INTERVAL,
    CONF_DEVICE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_DEVICE_INTERVAL,
    OperatingMode,
)
from .coordinator import (
    AnodeAPIClient,
    AnodeStatusCoordinator,
    AnodeDeviceCoordinator,
    AnodeModeCoordinator,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.NUMBER,
]

SERVICE_SET_OVERRIDE = "set_override"
SERVICE_SET_OVERRIDE_SCHEMA = vol.Schema({
    vol.Required("hub_id"): cv.string,
    vol.Required("mode"): vol.In([m.value for m in OperatingMode]),
    vol.Required("duration"): vol.All(vol.Coerce(int), vol.Range(min=0)),
})


def _sync_aliases_to_registry(
    hass: HomeAssistant, hub_id: str, status_data: dict
) -> None:
    """Push web-UI device aliases into HA's device registry.

    DeviceInfo only sets the device name when a device is first created. On
    upgrade — and on any later web-UI rename — the integration must explicitly
    update the registry. We update `name`, not `name_by_user`, so manual
    renames in HA still win in the UI.
    """
    device_registry = dr.async_get(hass)

    def _update(identifier: str, alias: str | None, default_name: str) -> None:
        desired = alias or default_name
        device = device_registry.async_get_device(identifiers={(DOMAIN, identifier)})
        if device is None or device.name == desired:
            return
        device_registry.async_update_device(device.id, name=desired)

    hub_alias = (status_data.get("hub") or {}).get("alias")
    _update(hub_id, hub_alias, f"Anode Hub {hub_id}")

    for battery in status_data.get("battery", []) or []:
        bid = battery.get("id")
        if bid:
            _update(bid, battery.get("alias"), f"Anode {bid}")

    for meter in status_data.get("meter", []) or []:
        mid = meter.get("id")
        if mid:
            _update(mid, meter.get("alias"), f"Anode Meter {mid}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Anode from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get configuration
    email = entry.data[CONF_EMAIL]
    api_key = entry.data[CONF_API_KEY]
    hub_id = entry.data[CONF_HUB_ID]

    # Get options with defaults
    status_interval = entry.options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)
    device_interval = entry.options.get(CONF_DEVICE_INTERVAL, DEFAULT_DEVICE_INTERVAL)

    # Create API client
    api_client = AnodeAPIClient(hass, email, api_key, hub_id)

    # Create coordinators
    status_coordinator = AnodeStatusCoordinator(hass, api_client, status_interval)
    device_coordinator = AnodeDeviceCoordinator(hass, api_client, device_interval)
    mode_coordinator = AnodeModeCoordinator(hass, api_client)

    # Initial data fetch
    await status_coordinator.async_config_entry_first_refresh()
    await mode_coordinator.async_config_entry_first_refresh()

    # Extract device IDs from status data
    status_data = status_coordinator.data
    battery_ids = [battery["id"] for battery in status_data.get("battery", [])]
    meter_ids = [meter["id"] for meter in status_data.get("meter", [])]

    # Set device IDs in the device coordinator
    device_coordinator.set_device_ids(battery_ids, meter_ids)

    # Fetch device data
    if battery_ids or meter_ids:
        await device_coordinator.async_config_entry_first_refresh()

    # Store coordinators and API client
    hass.data[DOMAIN][entry.entry_id] = {
        "api_client": api_client,
        "status_coordinator": status_coordinator,
        "device_coordinator": device_coordinator,
        "mode_coordinator": mode_coordinator,
    }

    # Register hub device
    hub_info = status_data.get("hub", {}) or {}
    hub_alias = hub_info.get("alias")
    hub_name = hub_alias or f"Anode Hub {hub_id}"
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_id)},
        manufacturer="Anode",
        name=hub_name,
        model="Hub",
        sw_version=hub_info.get("version", "unknown"),
    )

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Push aliases now (battery/meter devices were just created by their
    # entities' DeviceInfo) and on every subsequent status refresh so live
    # web-UI renames propagate without an HA reload.
    _sync_aliases_to_registry(hass, hub_id, status_coordinator.data)
    entry.async_on_unload(
        status_coordinator.async_add_listener(
            lambda: _sync_aliases_to_registry(
                hass, hub_id, status_coordinator.data or {}
            )
        )
    )

    # Register override service (only once, not per entry)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_OVERRIDE):
        async def handle_set_override(call: ServiceCall) -> None:
            """Handle set_override service call."""
            target_hub_id = call.data["hub_id"]
            mode = call.data["mode"]
            duration = call.data["duration"]

            # Find the api_client for this hub_id
            for entry_data in hass.data[DOMAIN].values():
                if not isinstance(entry_data, dict):
                    continue
                client: AnodeAPIClient = entry_data.get("api_client")
                if client and client.hub_id == target_hub_id:
                    await client.set_override(mode, duration)
                    mode_coord: AnodeModeCoordinator = entry_data.get("mode_coordinator")
                    if mode_coord:
                        await mode_coord.async_request_refresh_soon()
                    return
            _LOGGER.error("No hub found with id: %s", target_hub_id)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_OVERRIDE,
            handle_set_override,
            schema=SERVICE_SET_OVERRIDE_SCHEMA,
        )

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        # Remove service and domain key when no more entries remain
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_OVERRIDE)
            hass.data.pop(DOMAIN)

    return unload_ok

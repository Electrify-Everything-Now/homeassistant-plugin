"""The Anode Battery integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_HUB_ID,
    CONF_STATUS_INTERVAL,
    CONF_DEVICE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_DEVICE_INTERVAL,
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
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Anode Battery from a config entry."""
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

    # Set device IDs in device coordinator
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
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_id)},
        manufacturer="Anode",
        name=f"Anode Hub {hub_id}",
        model="Hub",
        sw_version=status_data.get("hub", {}).get("version", "unknown"),
    )

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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

    return unload_ok

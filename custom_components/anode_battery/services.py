"""Actions for the Anode integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import OperatingMode
from .const import CONF_HUB_ID, DOMAIN
from .coordinator import AnodeConfigEntry
from .entity import async_run_command

SERVICE_SET_OVERRIDE = "set_override"
SERVICE_CANCEL_OVERRIDE = "cancel_override"

ATTR_DEVICE_ID = "device_id"
ATTR_HUB_ID = "hub_id"
ATTR_MODE = "mode"
ATTR_DURATION = "duration"

_TARGET = {
    vol.Exclusive(ATTR_DEVICE_ID, "target"): cv.string,
    # Deprecated: kept so automations written for earlier releases keep working.
    vol.Exclusive(ATTR_HUB_ID, "target"): cv.string,
}

SET_OVERRIDE_SCHEMA = vol.All(
    vol.Schema(
        {
            **_TARGET,
            vol.Required(ATTR_MODE): vol.All(cv.string, vol.Upper, vol.Coerce(OperatingMode)),
            vol.Required(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }
    ),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_HUB_ID),
)

CANCEL_OVERRIDE_SCHEMA = vol.All(
    vol.Schema(_TARGET),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_HUB_ID),
)


@callback
def _async_get_entry(hass: HomeAssistant, call: ServiceCall) -> AnodeConfigEntry:
    """Find the loaded hub an action targets."""
    if device_id := call.data.get(ATTR_DEVICE_ID):
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": device_id},
            )
        candidates = [
            entry
            for entry_id in device.config_entries
            if (entry := hass.config_entries.async_get_entry(entry_id)) and entry.domain == DOMAIN
        ]
        target = device_id
    else:
        target = call.data[ATTR_HUB_ID]
        candidates = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_HUB_ID) == target
        ]
    if not candidates:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="hub_not_found",
            translation_placeholders={"target": target},
        )
    entry = candidates[0]
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="hub_not_loaded",
            translation_placeholders={"hub_id": entry.data[CONF_HUB_ID]},
        )
    return entry


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def set_override(call: ServiceCall) -> None:
        entry = _async_get_entry(hass, call)
        runtime = entry.runtime_data
        mode: OperatingMode = call.data[ATTR_MODE]
        duration: int = call.data[ATTR_DURATION]
        await async_run_command(
            hass, entry, runtime.client.set_override(runtime.hub_id, mode, duration)
        )
        # A zero duration ends the override on the hub.
        runtime.mode.async_note_override(mode if duration else None)

    async def cancel_override(call: ServiceCall) -> None:
        entry = _async_get_entry(hass, call)
        runtime = entry.runtime_data
        await async_run_command(hass, entry, runtime.client.cancel_override(runtime.hub_id))
        runtime.mode.async_note_override(None)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_OVERRIDE, set_override, schema=SET_OVERRIDE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_OVERRIDE, cancel_override, schema=CANCEL_OVERRIDE_SCHEMA
    )

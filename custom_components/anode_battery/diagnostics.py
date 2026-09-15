"""Diagnostics for the Anode integration."""
from __future__ import annotations

import dataclasses
from datetime import datetime, time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .coordinator import AnodeConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_EMAIL}


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, time)):
        return value.isoformat()
    return value


def _coordinator(coordinator: DataUpdateCoordinator) -> dict[str, Any]:
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "update_interval": str(coordinator.update_interval),
        "data": _jsonable(coordinator.data),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AnodeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    return {
        "entry": {
            "version": f"{entry.version}.{entry.minor_version}",
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "override_duration_min": runtime.override_duration_min,
        "status": _coordinator(runtime.status),
        "telemetry": _coordinator(runtime.telemetry),
        "mode": _coordinator(runtime.mode),
        "settings": _coordinator(runtime.settings),
    }

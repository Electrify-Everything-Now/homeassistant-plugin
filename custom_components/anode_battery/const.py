"""Constants for the Anode integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "anode_battery"
MANUFACTURER: Final = "Anode"

CONF_HUB_ID: Final = "hub_id"

# Options
CONF_STATUS_INTERVAL: Final = "status_update_interval"
CONF_DEVICE_INTERVAL: Final = "device_update_interval"

DEFAULT_STATUS_INTERVAL: Final = 120
DEFAULT_DEVICE_INTERVAL: Final = 30
MIN_UPDATE_INTERVAL: Final = 10

MODE_POLL_INTERVAL: Final = timedelta(minutes=5)
SETTINGS_POLL_INTERVAL: Final = timedelta(minutes=10)
# Wait this long after a schedule boundary or an override command before
# re-reading the mode, so we see the mode the hub switched to.
MODE_SETTLE_DELAY: Final = timedelta(seconds=5)

DEFAULT_OVERRIDE_DURATION_MIN: Final = 60
MAX_OVERRIDE_DURATION_MIN: Final = 7 * 24 * 60

# Energy counters can wobble by a few Wh between polls; ignore drops smaller
# than this rather than logging them.
ENERGY_DROP_TOLERANCE_KWH: Final = 0.01

# Unique-id suffixes of entities removed in config entry version 1.2.
REMOVED_HUB_SENSOR_KEYS: Final = (
    "battery_charge_energy_today",
    "battery_discharge_energy_today",
    "grid_import_energy_today",
    "grid_export_energy_today",
    "house_energy_today",
)
REMOVED_HUB_SELECT_KEYS: Final = (
    "charge_override",
    "discharge_override",
    "idle_override",
    "match_override",
)

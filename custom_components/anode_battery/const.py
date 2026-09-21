"""Constants for the Anode integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "anode_battery"
MANUFACTURER: Final = "Anode"

CONF_HUB_ID: Final = "hub_id"
# How the entry authenticates. Entries from before linking have no value and
# use an API key.
CONF_AUTH_TYPE: Final = "auth_type"
AUTH_API_KEY: Final = "api_key"
AUTH_LINK: Final = "link"
# Set for linked entries whose key may only read.
CONF_READ_ONLY: Final = "read_only"
# Set for linked entries whose key may install firmware. API keys made by hand
# never set it: nothing tells us what such a key may do.
CONF_FIRMWARE: Final = "firmware"
# Lower bound on how often to poll for an approved link, in seconds.
LINK_MIN_POLL_INTERVAL: Final = 1

# Options
CONF_STATUS_INTERVAL: Final = "status_update_interval"
CONF_DEVICE_INTERVAL: Final = "device_update_interval"

DEFAULT_STATUS_INTERVAL: Final = 120
DEFAULT_DEVICE_INTERVAL: Final = 30
MIN_UPDATE_INTERVAL: Final = 10

MODE_POLL_INTERVAL: Final = timedelta(minutes=5)
SETTINGS_POLL_INTERVAL: Final = timedelta(minutes=10)
# BMS data needs one request per battery on current firmware, so it is read
# less often than power and energy. Batteries that report none are re-checked
# in case their firmware is updated.
BMS_POLL_INTERVAL: Final = timedelta(minutes=2)
BMS_UNSUPPORTED_RECHECK: Final = timedelta(hours=1)
# Wait this long after a schedule boundary or an override command before
# re-reading the mode, so we see the mode the hub switched to.
MODE_SETTLE_DELAY: Final = timedelta(seconds=5)

# While a firmware update runs: how often to ask for progress (each request
# itself waits up to a few seconds for the hub to report), and how often to
# re-read status to see it finish.
OTA_PROGRESS_INTERVAL: Final = timedelta(seconds=2)
OTA_STATUS_INTERVAL: Final = timedelta(seconds=20)
# How long an update Home Assistant started shows as installing before status
# reports it. The hub starts it at once, so this only covers the status read.
OTA_PENDING_TIMEOUT: Final = timedelta(minutes=2)

DEFAULT_OVERRIDE_DURATION_MIN: Final = 60
MAX_OVERRIDE_DURATION_MIN: Final = 7 * 24 * 60

# Energy counters can wobble by a few Wh between polls; ignore drops smaller
# than this rather than logging them.
ENERGY_DROP_TOLERANCE_KWH: Final = 0.01

# Unique-id suffixes of the selects removed in config entry version 1.2.
REMOVED_HUB_SELECT_KEYS: Final = (
    "charge_override",
    "discharge_override",
    "idle_override",
    "match_override",
)

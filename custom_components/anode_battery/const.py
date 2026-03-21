"""Constants for the Anode integration."""
from enum import StrEnum
from typing import Final

DOMAIN: Final = "anode_battery"

# API Configuration
API_BASE_URL: Final = "https://amp.anode.energy"
API_TIMEOUT: Final = 30

# Configuration Keys
CONF_API_KEY: Final = "api_key"
CONF_HUB_ID: Final = "hub_id"
CONF_EMAIL: Final = "email"

# Options Keys
CONF_STATUS_INTERVAL: Final = "status_update_interval"
CONF_DEVICE_INTERVAL: Final = "device_update_interval"

# Default Values
DEFAULT_STATUS_INTERVAL: Final = 120  # 2 minutes
DEFAULT_DEVICE_INTERVAL: Final = 10   # 10 seconds
MIN_UPDATE_INTERVAL: Final = 10       # Minimum 10 seconds

# Device Classes
DEVICE_TYPE_HUB: Final = "hub"
DEVICE_TYPE_BATTERY: Final = "battery"
DEVICE_TYPE_METER: Final = "meter"


class OperatingMode(StrEnum):
    """Operating modes for the hub."""
    CHARGE = "CHARGE"
    DISCHARGE = "DISCHARGE"
    IDLE = "IDLE"
    MATCH = "MATCH"


class MeterType(StrEnum):
    """Meter types."""
    PRIMARY = "PRIMARY"
    LOAD = "LOAD"
    MONITOR = "MONITOR"
    EXT_INVERTER = "EXT_INVERTER"


# Mode Override Time Options (in seconds)
OVERRIDE_TIME_OPTIONS: Final = {
    "15_min": 900,
    "30_min": 1800,
    "1_hour": 3600,
    "2_hours": 7200,
    "3_hours": 10800,
    "4_hours": 14400,
}

# Override Time Labels
OVERRIDE_TIME_LABELS: Final = {
    "15_min": "15 minutes",
    "30_min": "30 minutes",
    "1_hour": "1 hour",
    "2_hours": "2 hours",
    "3_hours": "3 hours",
    "4_hours": "4 hours",
}

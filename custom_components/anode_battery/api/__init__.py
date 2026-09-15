"""Client library for the Anode cloud API.

Deliberately free of Home Assistant imports so it can be published to PyPI
as a standalone package and listed in the manifest's ``requirements``.
"""
from .client import API_BASE_URL, AnodeClient
from .exceptions import (
    AnodeAuthError,
    AnodeCommandError,
    AnodeConnectionError,
    AnodeError,
    AnodeForbiddenError,
    AnodeHubOfflineError,
    AnodeNotFoundError,
    AnodeRateLimitError,
    AnodeResponseError,
)
from .models import (
    AccountHub,
    BatteryReading,
    BmsReading,
    DeviceMetadata,
    HubStatus,
    MeterReading,
    MeterType,
    OperatingMode,
    PowerLimit,
    PowerLimitKey,
    ScheduleSlot,
    SocLimits,
    SubDevice,
)

__all__ = [
    "API_BASE_URL",
    "AccountHub",
    "AnodeAuthError",
    "AnodeClient",
    "AnodeCommandError",
    "AnodeConnectionError",
    "AnodeError",
    "AnodeForbiddenError",
    "AnodeHubOfflineError",
    "AnodeNotFoundError",
    "AnodeRateLimitError",
    "AnodeResponseError",
    "BatteryReading",
    "BmsReading",
    "DeviceMetadata",
    "HubStatus",
    "MeterReading",
    "MeterType",
    "OperatingMode",
    "PowerLimit",
    "PowerLimitKey",
    "ScheduleSlot",
    "SocLimits",
    "SubDevice",
]

"""Typed models for Anode cloud API responses.

Every response is parsed here, once, into dataclasses with normalised units
(watts, kWh, percent). Nothing outside this package reads raw API dicts, so an
API shape change breaks one parser instead of every entity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time
from enum import StrEnum
import json
import logging
from typing import Any

from .exceptions import AnodeCommandError, AnodeResponseError

_LOGGER = logging.getLogger(__name__)


class OperatingMode(StrEnum):
    """Hub operating modes."""

    CHARGE = "CHARGE"
    DISCHARGE = "DISCHARGE"
    IDLE = "IDLE"
    MATCH = "MATCH"


class MeterType(StrEnum):
    """Meter types reported in hub status."""

    PRIMARY = "PRIMARY"
    LOAD = "LOAD"
    MONITOR = "MONITOR"
    EXT_INVERTER = "EXT_INVERTER"


class PowerLimitKey(StrEnum):
    """Config keys holding fleet charge/discharge power limits."""

    MAX_CHARGE = "maxChargePower"
    MAX_DISCHARGE = "maxDischargePower"


# Unit factors, keyed by lower-cased unit string.
_POWER_TO_W = {"w": 1.0, "kw": 1000.0, "": 1.0}
# Hardware counters are reported in deci-watt-hours (0.1 Wh).
_ENERGY_TO_KWH = {"dwh": 1 / 10000, "wh": 1 / 1000, "kwh": 1.0}


def require_dict(data: Any, what: str) -> dict[str, Any]:
    """Return data if it is a JSON object, otherwise raise."""
    if not isinstance(data, dict):
        raise AnodeResponseError(
            f"Expected an object for {what}, got {type(data).__name__}"
        )
    return data


def raise_for_hub_failure(data: Any, action: str) -> None:
    """Raise if a hub acknowledgement reports failure.

    Hub acknowledgements look like ``{"status": false, "info": "..."}``. The
    override endpoint wraps the hub's raw JSON string in ``{"mode": "..."}``.
    """
    if isinstance(data, dict) and isinstance(data.get("mode"), str):
        try:
            data = json.loads(data["mode"])
        except ValueError:
            return
    if isinstance(data, dict) and data.get("status") is False:
        info = data.get("info")
        raise AnodeCommandError(f"Hub rejected {action}: {info}" if info else f"Hub rejected {action}")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _enum[E: StrEnum](enum: type[E], value: Any) -> E | None:
    try:
        return enum(value)
    except ValueError:
        return None


def _unit_value(data: dict[str, Any], key: str, factors: dict[str, float]) -> float | None:
    """Read a ``{"value": n, "unit": "..."}`` field and convert its unit."""
    entry = data.get(key)
    if not isinstance(entry, dict):
        return None
    value = _number(entry.get("value"))
    if value is None:
        return None
    unit = entry.get("unit", "")
    factor = factors.get(unit.lower() if isinstance(unit, str) else "")
    if factor is None:
        _LOGGER.warning("Ignoring %s with unrecognised unit %r", key, unit)
        return None
    return value * factor


@dataclass(frozen=True, slots=True)
class DeviceMetadata:
    """Web-UI metadata for one device."""

    friendly_id: str
    alias: str | None
    meter_purpose: str | None


def parse_device_metadata(data: Any) -> dict[str, DeviceMetadata]:
    """Parse ``GET /api/user/device-metadata/{hub}``."""
    data = require_dict(data, "device metadata")
    if data.get("success") is False:
        raise AnodeCommandError(data.get("message") or "Device metadata unavailable")
    result: dict[str, DeviceMetadata] = {}
    for item in data.get("metadata") or []:
        if not isinstance(item, dict) or not (fid := _str(item.get("friendlyId"))):
            continue
        result[fid] = DeviceMetadata(
            friendly_id=fid,
            alias=_str(item.get("alias")),
            meter_purpose=_str(item.get("meterPurpose")),
        )
    return result


@dataclass(frozen=True, slots=True)
class SubDevice:
    """A battery or meter as listed in hub status."""

    id: str
    version: str | None = None
    uptime_ms: int | None = None
    meter_type: MeterType | None = None
    parent_meter: str | None = None
    online: bool | None = None
    alias: str | None = None
    meter_purpose: str | None = None

    @classmethod
    def from_api(cls, data: Any) -> SubDevice:
        """Parse one entry of the status ``battery``/``meter`` arrays."""
        data = require_dict(data, "status device")
        if not (device_id := _str(data.get("id"))):
            raise AnodeResponseError("Status device entry is missing an id")
        return cls(
            id=device_id,
            version=_str(data.get("version")),
            uptime_ms=_int(data.get("uptime")),
            meter_type=_enum(MeterType, data.get("type")),
            parent_meter=_str(data.get("parentMeter")),
            online=_bool(data.get("online")),
        )

    @property
    def is_grid_meter(self) -> bool:
        """Whether this meter measures the grid connection."""
        return self.meter_type is MeterType.PRIMARY or self.meter_purpose == "primary"

    @property
    def is_generation_meter(self) -> bool:
        """Whether this meter measures a generation source such as solar."""
        if self.is_grid_meter:
            return False
        return self.meter_type is MeterType.EXT_INVERTER or self.meter_purpose == "solar"


@dataclass(frozen=True, slots=True)
class HubStatus:
    """Parsed ``GET /api/device/status/{hub}``."""

    hub_id: str
    online: bool
    version: str | None
    uptime_ms: int | None
    batteries: dict[str, SubDevice]
    meters: dict[str, SubDevice]
    alias: str | None = None

    @classmethod
    def from_api(cls, hub_id: str, data: Any) -> HubStatus:
        """Parse a status response."""
        data = require_dict(data, "hub status")
        hub = data.get("hub") if isinstance(data.get("hub"), dict) else {}
        return cls(
            hub_id=hub_id,
            online=data.get("status") is True,
            version=_str(hub.get("version")),
            uptime_ms=_int(hub.get("uptime")),
            batteries=_parse_sub_devices(data.get("battery")),
            meters=_parse_sub_devices(data.get("meter")),
        )

    def with_metadata(self, metadata: dict[str, DeviceMetadata]) -> HubStatus:
        """Return a copy with web-UI aliases and meter purposes applied."""

        def apply(device: SubDevice) -> SubDevice:
            meta = metadata.get(device.id)
            if meta is None:
                return device
            return replace(device, alias=meta.alias, meter_purpose=meta.meter_purpose)

        hub_meta = metadata.get(self.hub_id)
        return replace(
            self,
            alias=hub_meta.alias if hub_meta else self.alias,
            batteries={k: apply(v) for k, v in self.batteries.items()},
            meters={k: apply(v) for k, v in self.meters.items()},
        )

    @property
    def grid_meters(self) -> list[SubDevice]:
        """Meters on the grid connection."""
        return [m for m in self.meters.values() if m.is_grid_meter]

    @property
    def generation_meters(self) -> list[SubDevice]:
        """Meters on generation sources."""
        return [m for m in self.meters.values() if m.is_generation_meter]


def _parse_sub_devices(items: Any) -> dict[str, SubDevice]:
    devices: dict[str, SubDevice] = {}
    for item in items if isinstance(items, list) else []:
        try:
            device = SubDevice.from_api(item)
        except AnodeResponseError as err:
            _LOGGER.warning("Skipping malformed status entry: %s", err)
            continue
        devices[device.id] = device
    return devices


@dataclass(frozen=True, slots=True)
class BatteryReading:
    """Live readings for one battery."""

    power_w: float
    soc_pct: float
    power_status: str | None = None
    capacity_ah: float | None = None
    nominal_voltage_v: float | None = None
    calibrated: bool | None = None
    charge_energy_kwh: float | None = None
    discharge_energy_kwh: float | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> BatteryReading:
        """Parse one battery reading."""
        power = _unit_value(data, "power", _POWER_TO_W)
        soc = _number((data.get("soc") or {}).get("value")) if isinstance(data.get("soc"), dict) else None
        if power is None or soc is None:
            raise AnodeResponseError("Battery reading is missing power or soc")
        capacity = data.get("capacity") if isinstance(data.get("capacity"), dict) else {}
        return cls(
            power_w=power,
            soc_pct=soc,
            power_status=_str(data.get("powerStatus")),
            capacity_ah=_number(capacity.get("value")),
            nominal_voltage_v=_number(capacity.get("nominalVoltage")) or None,
            calibrated=_bool(capacity.get("calibrated")),
            charge_energy_kwh=_unit_value(data, "importEnergy", _ENERGY_TO_KWH),
            discharge_energy_kwh=_unit_value(data, "exportEnergy", _ENERGY_TO_KWH),
        )

    @property
    def capacity_remaining_ah(self) -> float | None:
        """Charge remaining in amp-hours."""
        if self.capacity_ah is None:
            return None
        return self.capacity_ah * self.soc_pct / 100

    @property
    def energy_capacity_wh(self) -> float | None:
        """Total energy capacity in watt-hours."""
        if self.capacity_ah is None or self.nominal_voltage_v is None:
            return None
        return self.capacity_ah * self.nominal_voltage_v

    @property
    def energy_remaining_wh(self) -> float | None:
        """Energy remaining in watt-hours."""
        if (capacity := self.energy_capacity_wh) is None:
            return None
        return capacity * self.soc_pct / 100


@dataclass(frozen=True, slots=True)
class MeterReading:
    """Live readings for one meter."""

    power_w: float
    voltage_v: float | None = None
    current_a: float | None = None
    power_factor: float | None = None
    import_energy_kwh: float | None = None
    export_energy_kwh: float | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> MeterReading:
        """Parse one meter reading."""
        power = _unit_value(data, "power", _POWER_TO_W)
        if power is None:
            raise AnodeResponseError("Meter reading is missing power")

        def plain(key: str) -> float | None:
            entry = data.get(key)
            return _number(entry.get("value")) if isinstance(entry, dict) else None

        return cls(
            power_w=power,
            voltage_v=plain("voltage"),
            current_a=plain("current"),
            power_factor=plain("powerFactor"),
            import_energy_kwh=_unit_value(data, "importEnergy", _ENERGY_TO_KWH),
            export_energy_kwh=_unit_value(data, "exportEnergy", _ENERGY_TO_KWH),
        )


def _parse_readings[R](data: Any, kind: str, parse: Any) -> dict[str, R]:
    """Parse a batch battery/meter response: a JSON array with an id per item."""
    if isinstance(data, dict):
        raise_for_hub_failure(data, f"the {kind} read")
    if not isinstance(data, list):
        raise AnodeResponseError(f"Expected a list of {kind} readings")
    readings: dict[str, R] = {}
    for item in data:
        if not isinstance(item, dict) or not (device_id := _str(item.get("id"))):
            _LOGGER.warning("Skipping %s reading without an id", kind)
            continue
        try:
            readings[device_id] = parse(item)
        except AnodeResponseError as err:
            _LOGGER.warning("Skipping %s %s: %s", kind, device_id, err)
    return readings


def parse_batteries(data: Any) -> dict[str, BatteryReading]:
    """Parse ``GET /api/device/battery/{hub}`` (all batteries)."""
    return _parse_readings(data, "battery", BatteryReading.from_api)


def parse_meters(data: Any) -> dict[str, MeterReading]:
    """Parse ``GET /api/device/meter/{hub}`` (all meters)."""
    return _parse_readings(data, "meter", MeterReading.from_api)


def _slot_time(data: Any) -> time | None:
    if not isinstance(data, dict):
        return None
    hour, minute, second = (_int(data.get(k, 0)) for k in ("hour", "minute", "second"))
    if hour is None or minute is None or second is None:
        return None
    try:
        return time(hour % 24, minute, second)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ScheduleSlot:
    """One schedule slot, in the hub's local time."""

    begin: time
    end: time
    mode: OperatingMode
    target_soc: int | None = None

    def contains(self, moment: time) -> bool:
        """Whether a local time-of-day falls inside this slot.

        Mirrors the firmware: begin is inclusive, end exclusive, and a slot
        whose end is earlier than its begin runs over midnight.
        """
        if self.begin <= self.end:
            return self.begin <= moment < self.end
        return moment >= self.begin or moment < self.end


def parse_schedule(data: Any) -> list[ScheduleSlot]:
    """Parse ``GET /api/device/schedule/{hub}``."""
    data = require_dict(data, "schedule")
    raise_for_hub_failure(data, "the schedule read")
    slots: list[ScheduleSlot] = []
    for item in data.get("schedule") or []:
        if not isinstance(item, dict):
            continue
        begin, end = _slot_time(item.get("begin")), _slot_time(item.get("end"))
        mode = _enum(OperatingMode, item.get("mode"))
        if begin is None or end is None or mode is None:
            _LOGGER.warning("Skipping malformed schedule slot: %s", item)
            continue
        # The firmware treats an all-zero slot as an empty placeholder.
        if begin == end:
            continue
        slots.append(ScheduleSlot(begin, end, mode, _int(item.get("targetSoc")) or None))
    return slots


@dataclass(frozen=True, slots=True)
class SocLimits:
    """Per-battery state-of-charge window."""

    min_soc: int
    max_soc: int


def parse_soc_limits(value: Any) -> dict[str, SocLimits]:
    """Parse the ``value`` of the ``socConfig`` config key."""
    result: dict[str, SocLimits] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or item.get("status") is False:
            continue
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        device_id = _str(item.get("id"))
        min_soc, max_soc = _int(config.get("minSoc")), _int(config.get("maxSoc"))
        if device_id and min_soc is not None and max_soc is not None:
            result[device_id] = SocLimits(min_soc, max_soc)
    return result


@dataclass(frozen=True, slots=True)
class PowerLimit:
    """A fleet charge or discharge power limit.

    Current firmware stores the limit as a percent of fleet power and also
    reports the watts that percent currently means. Older firmware stores
    plain watts, in which case ``percent`` is None.
    """

    watts: float | None
    percent: float | None

    @property
    def is_percent_based(self) -> bool:
        """Whether the hub stores this limit as a percent."""
        return self.percent is not None


def parse_power_limit(value: Any) -> PowerLimit:
    """Parse the ``value`` of ``maxChargePower``/``maxDischargePower``."""
    if isinstance(value, dict):
        return PowerLimit(watts=_number(value.get("watts")), percent=_number(value.get("percent")))
    if (watts := _number(value)) is not None:
        return PowerLimit(watts=watts, percent=None)
    raise AnodeResponseError(f"Unrecognised power limit value: {value!r}")


@dataclass(frozen=True, slots=True)
class AccountHub:
    """The hub linked to an account, from ``GET /api/user/devices``."""

    hub_id: str
    alias: str | None


def parse_account_hub(data: Any) -> AccountHub:
    """Parse ``GET /api/user/devices``."""
    data = require_dict(data, "account devices")
    hub = require_dict(data.get("hub"), "account hub")
    if not (hub_id := _str(hub.get("id"))):
        raise AnodeResponseError("Account hub has no id")
    return AccountHub(hub_id=hub_id, alias=_str(hub.get("alias")))

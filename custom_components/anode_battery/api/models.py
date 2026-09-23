"""Typed models for Anode cloud API responses.

Every response is parsed here, once, into dataclasses with normalised units
(watts, kWh, percent). Nothing outside this package reads raw API dicts, so an
API shape change breaks one parser instead of every entity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time
from enum import IntEnum, StrEnum
import json
import logging
from typing import Any, Final

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


class ProductCode(IntEnum):
    """The product catalogue's codes, as release notes are keyed by.

    The hub reports what each device is by which array it appears in, so
    nothing here has to be decoded out of a device id.
    """

    HUB = 1
    METER = 2
    BATTERY = 3
    REPEATER = 4


# Unit factors, keyed by lower-cased unit string.
_POWER_TO_W = {"w": 1.0, "kw": 1000.0, "": 1.0}
# Hardware counters are reported in deci-watt-hours (0.1 Wh).
_ENERGY_TO_KWH = {"dwh": 1 / 10000, "wh": 1 / 1000, "kwh": 1.0}
_VOLTAGE_TO_V = {"v": 1.0, "mv": 1 / 1000}


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
    """A battery, meter or repeater as listed in hub status."""

    id: str
    version: str | None = None
    uptime_ms: int | None = None
    meter_type: MeterType | None = None
    parent_meter: str | None = None
    online: bool | None = None
    alias: str | None = None
    meter_purpose: str | None = None
    #: What the firmware train holds for this device, or None when the server
    #: could not work that out. See ``HubStatus.latest_version``.
    latest_version: str | None = None
    update_available: bool = False
    ota_in_progress: bool = False

    @classmethod
    def from_api(cls, data: Any) -> SubDevice:
        """Parse one entry of the status ``battery``/``meter``/``repeater`` arrays."""
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
            latest_version=_str(data.get("latestVersion")),
            update_available=data.get("updateAvailable") is True,
            ota_in_progress=data.get("otaInProgress") is True,
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
    #: Radio repeaters. They carry no readings, only what status reports.
    repeaters: dict[str, SubDevice]
    alias: str | None = None
    #: The newest image the account's firmware train holds for this hardware.
    #:
    #: The server joins the versions the hub reports against the images on the
    #: OTA share, so nothing here compares version strings. It is None whenever
    #: the server could not reach an answer — an unknown product, no image for
    #: this hardware revision, a version string it could not parse — and that
    #: absence is deliberately not filled in with the installed version, which
    #: would read as "up to date" when in fact nobody knows.
    latest_version: str | None = None
    #: Whether the train holds something strictly newer. Not the same question
    #: as ``latest_version != version``: a device ahead of its train is not
    #: offered a downgrade.
    update_available: bool = False
    ota_in_progress: bool = False

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
            repeaters=_parse_sub_devices(data.get("repeater")),
            latest_version=_str(hub.get("latestVersion")),
            update_available=hub.get("updateAvailable") is True,
            ota_in_progress=hub.get("otaInProgress") is True,
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
            repeaters={k: apply(v) for k, v in self.repeaters.items()},
        )

    @property
    def sub_devices(self) -> list[SubDevice]:
        """Every battery, meter and repeater paired to the hub."""
        return [*self.batteries.values(), *self.meters.values(), *self.repeaters.values()]

    def sub_device(self, device_id: str) -> SubDevice | None:
        """A battery, meter or repeater by id."""
        return (
            self.batteries.get(device_id)
            or self.meters.get(device_id)
            or self.repeaters.get(device_id)
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
class ReleaseNote:
    """What changed in one firmware version, from ``GET /device/release-notes``.

    Which stream the note is taken from is the server's decision, made from the
    account's own firmware preference, so nothing here names a stream.
    """

    version: str
    summary: str
    whats_new: str | None = None
    whats_fixed: str | None = None

    @classmethod
    def from_api(cls, data: Any) -> ReleaseNote:
        """Parse a release note response."""
        data = require_dict(data, "release note")
        version, summary = _str(data.get("version")), _str(data.get("summary"))
        if not version or not summary:
            raise AnodeResponseError("Release note is missing its version or summary")
        return cls(
            version=version,
            summary=summary,
            whats_new=_str(data.get("whatsNew")),
            whats_fixed=_str(data.get("whatsFixed")),
        )


@dataclass(frozen=True, slots=True)
class FirmwareUpgrade:
    """One device an update-to-latest sent an image to."""

    device_id: str
    from_version: str
    to_version: str


@dataclass(frozen=True, slots=True)
class FirmwareUpdateResult:
    """Parsed ``PUT /device/ota/{hub}/latest``.

    The server picks each image from the owner's train, so the request names no
    image and ``updates`` is the only record of what was sent. Empty means every
    device asked about was already current, and nothing was sent.
    """

    stream: str | None
    updates: tuple[FirmwareUpgrade, ...]

    @classmethod
    def from_api(cls, data: Any) -> FirmwareUpdateResult:
        """Parse an update-to-latest response."""
        data = require_dict(data, "firmware update")
        updates = data.get("updates")
        if not isinstance(updates, list):
            raise AnodeResponseError("Firmware update response is missing its updates")
        parsed = []
        for item in updates:
            if not isinstance(item, dict):
                continue
            device_id, old, new = (_str(item.get(key)) for key in ("id", "from", "to"))
            if device_id and old and new:
                parsed.append(FirmwareUpgrade(device_id, old, new))
        return cls(stream=_str(data.get("stream")), updates=tuple(parsed))


def parse_ota_progress(data: Any) -> int | None:
    """Return the percent from ``GET /device/ota/{hub}``, if it has one.

    The hub reports on whichever device it is flashing and does not say which:
    it only ever flashes one at a time, so the caller knows.
    """
    percent = _int(require_dict(data, "firmware progress").get("percent"))
    return max(0, min(100, percent)) if percent is not None else None


def _unit_values(data: dict[str, Any], key: str) -> tuple[list[float], str] | None:
    """Read a ``{"values": [...], "unit": "..."}`` field.

    The whole list is dropped if any entry is unreadable, so positions (cell
    or probe numbers) never shift.
    """
    entry = data.get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("values"), list):
        return None
    values = [_number(value) for value in entry["values"]]
    if any(value is None for value in values):
        _LOGGER.warning("Ignoring %s with unreadable values: %s", key, entry["values"])
        return None
    unit = entry.get("unit")
    return values, unit if isinstance(unit, str) else ""


def _to_celsius(value: float, unit: str) -> float | None:
    unit = unit.replace("°", "").strip().lower()
    if unit in ("c", "degc", "celsius"):
        return value
    if unit in ("f", "degf", "fahrenheit"):
        return (value - 32) * 5 / 9
    if unit in ("k", "kelvin"):
        return value - 273.15
    return None


def _temperatures(bms: dict[str, Any]) -> tuple[float, ...]:
    if (parsed := _unit_values(bms, "ntcTemp")) is None:
        return ()
    values, unit = parsed
    celsius = [_to_celsius(value, unit) for value in values]
    if any(value is None for value in celsius):
        _LOGGER.warning("Ignoring temperatures with unrecognised unit %r", unit)
        return ()
    return tuple(celsius)


def _cell_voltages(bms: dict[str, Any]) -> tuple[float, ...]:
    if (parsed := _unit_values(bms, "cells")) is None:
        return ()
    values, unit = parsed
    if (factor := _VOLTAGE_TO_V.get(unit.lower())) is None:
        _LOGGER.warning("Ignoring cell voltages with unrecognised unit %r", unit)
        return ()
    return tuple(value * factor for value in values)


@dataclass(frozen=True, slots=True)
class BmsReading:
    """Battery management system readings: pack voltage, probes and cells."""

    pack_voltage_v: float | None = None
    temperatures_c: tuple[float, ...] = ()
    cell_voltages_v: tuple[float, ...] = ()

    @classmethod
    def from_api(cls, data: Any) -> BmsReading | None:
        """Parse a ``bms`` block; None if it is missing or empty."""
        if not isinstance(data, dict):
            return None
        reading = cls(
            pack_voltage_v=_unit_value(data, "voltageAct", _VOLTAGE_TO_V),
            temperatures_c=_temperatures(data),
            cell_voltages_v=_cell_voltages(data),
        )
        if reading == cls():
            return None
        return reading


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
    # Absent on older battery firmware. Current firmware only includes it
    # when a battery is read on its own, not in the all-batteries read.
    bms: BmsReading | None = None

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
            bms=BmsReading.from_api(data.get("bms")),
        )

    @property
    def pack_voltage_v(self) -> float | None:
        """Measured pack voltage."""
        return self.bms.pack_voltage_v if self.bms else None

    @property
    def temperatures_c(self) -> tuple[float, ...]:
        """Temperature probe readings, in probe order."""
        return self.bms.temperatures_c if self.bms else ()

    @property
    def cell_voltages_v(self) -> tuple[float, ...]:
        """Cell voltages, in cell order."""
        return self.bms.cell_voltages_v if self.bms else ()

    @property
    def max_temperature_c(self) -> float | None:
        """Warmest temperature probe."""
        return max(self.temperatures_c) if self.temperatures_c else None

    @property
    def min_temperature_c(self) -> float | None:
        """Coolest temperature probe."""
        return min(self.temperatures_c) if self.temperatures_c else None

    @property
    def max_cell_voltage_v(self) -> float | None:
        """Highest cell voltage."""
        return max(self.cell_voltages_v) if self.cell_voltages_v else None

    @property
    def min_cell_voltage_v(self) -> float | None:
        """Lowest cell voltage."""
        return min(self.cell_voltages_v) if self.cell_voltages_v else None

    @property
    def cell_voltage_difference_mv(self) -> float | None:
        """Gap between the highest and lowest cell, in millivolts."""
        if not self.cell_voltages_v:
            return None
        return (max(self.cell_voltages_v) - min(self.cell_voltages_v)) * 1000

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
class LinkCode:
    """A pending account link, from ``POST /device-auth/request``."""

    device_code: str
    user_code: str
    verification_url: str
    verification_url_complete: str
    expires_in: int
    interval: int

    @classmethod
    def from_api(cls, data: Any) -> LinkCode:
        """Parse a link request response."""
        data = require_dict(data, "link request")
        device_code, user_code = _str(data.get("deviceCode")), _str(data.get("userCode"))
        url = _str(data.get("verificationUrl"))
        if not device_code or not user_code or not url:
            raise AnodeResponseError("Link request response is missing its codes")
        return cls(
            device_code=device_code,
            user_code=user_code,
            verification_url=url,
            verification_url_complete=_str(data.get("verificationUrlComplete")) or url,
            expires_in=_int(data.get("expiresIn")) or 900,
            interval=max(_int(data.get("interval")) or 0, 0),
        )


# What a key may reach. The full table is the Anode API's. Everything this
# integration reads is device:read, the overrides and limits it writes are
# device:control, and installing firmware is device:firmware: moving devices up
# to the latest image on the owner's own train, and nothing else.
SCOPE_DEVICE_READ: Final = "device:read"
SCOPE_DEVICE_CONTROL: Final = "device:control"
SCOPE_DEVICE_FIRMWARE: Final = "device:firmware"
# Never asked for, but a key holding it may also install firmware: it may write
# any image, so the latest one is a slice of that.
SCOPE_DEVICE_SERVICE: Final = "device:service"

# Scopes reach one hub rather than everything the approving account reaches.
BINDING_HUB: Final = "hub"


@dataclass(frozen=True, slots=True)
class LinkGrant:
    """An approved account link, from ``POST /device-auth/token``."""

    api_key: str
    email: str
    scopes: frozenset[str]
    binding: str
    #: The hub the key is bound to. None when it is bound to the account.
    hub_id: str | None

    @property
    def read_only(self) -> bool:
        """Whether the key may only read, so no controls are offered."""
        return SCOPE_DEVICE_CONTROL not in self.scopes

    @property
    def can_update_firmware(self) -> bool:
        """Whether the key may install the latest firmware."""
        return bool({SCOPE_DEVICE_FIRMWARE, SCOPE_DEVICE_SERVICE} & self.scopes)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> LinkGrant:
        """Parse an approved token response."""
        api_key, email = _str(data.get("apiKey")), _str(data.get("email"))
        if not api_key or not email:
            raise AnodeResponseError("Approved link response is missing its key or account")
        scopes = data.get("scopes")
        if not isinstance(scopes, list):
            raise AnodeResponseError("Approved link response is missing its scopes")
        return cls(
            api_key=api_key,
            email=email,
            scopes=frozenset(filter(None, (_str(scope) for scope in scopes))),
            binding=_str(data.get("binding")) or BINDING_HUB,
            hub_id=_str(data.get("hubId")),
        )


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

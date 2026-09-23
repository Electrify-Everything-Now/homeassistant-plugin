"""Sensor platform for the Anode integration."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .api import BatteryReading, HubStatus, MeterReading, OperatingMode, SubDevice
from .const import ENERGY_DROP_TOLERANCE_KWH
from .coordinator import (
    AnodeConfigEntry,
    AnodeModeCoordinator,
    AnodeRuntimeData,
    AnodeStatusCoordinator,
    AnodeTelemetryCoordinator,
    ModeState,
    Telemetry,
)
from .entity import AnodeEntity, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_MODES = [mode.value for mode in OperatingMode]
UNIT_AMP_HOUR = "Ah"


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


@dataclass(frozen=True, kw_only=True)
class AnodeHubStatusSensorDescription(SensorEntityDescription):
    """A hub sensor read from hub status."""

    value_fn: Callable[[HubStatus], StateType]


@dataclass(frozen=True, kw_only=True)
class AnodeModeSensorDescription(SensorEntityDescription):
    """A hub sensor read from the mode and schedule."""

    value_fn: Callable[[ModeState], StateType | datetime]


@dataclass(frozen=True, kw_only=True)
class AnodeSubDeviceSensorDescription(SensorEntityDescription):
    """A battery or meter sensor read from hub status."""

    value_fn: Callable[[SubDevice], StateType]
    exists_fn: Callable[[SubDevice], bool] = lambda _: True


@dataclass(frozen=True, kw_only=True)
class AnodeBatterySensorDescription(SensorEntityDescription):
    """A battery sensor read from telemetry."""

    value_fn: Callable[[BatteryReading], StateType]
    exists_fn: Callable[[BatteryReading], bool] = lambda _: True


@dataclass(frozen=True, kw_only=True)
class AnodeBatteryIndexedSensorDescription(SensorEntityDescription):
    """One sensor per value in a battery list reading, such as cell voltages."""

    values_fn: Callable[[BatteryReading], tuple[float, ...]]
    # Translation placeholder filled with the 1-based position.
    placeholder: str


@dataclass(frozen=True, kw_only=True)
class AnodeMeterSensorDescription(SensorEntityDescription):
    """A meter sensor read from telemetry."""

    value_fn: Callable[[MeterReading], StateType]


@dataclass(frozen=True, kw_only=True)
class AnodeHubTelemetrySensorDescription(SensorEntityDescription):
    """A hub sensor calculated from several devices' telemetry."""

    value_fn: Callable[[HubStatus, Telemetry], float | None]
    exists_fn: Callable[[HubStatus], bool]
    # Never publish a lower value than before, including across restarts.
    monotonic: bool = False


HUB_STATUS_SENSORS: tuple[AnodeHubStatusSensorDescription, ...] = (
    AnodeHubStatusSensorDescription(
        key="version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.version,
    ),
    AnodeHubStatusSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.uptime_ms,
    ),
)

MODE_SENSORS: tuple[AnodeModeSensorDescription, ...] = (
    AnodeModeSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=_MODES,
        value_fn=lambda state: state.mode.value if state.mode else None,
    ),
    AnodeModeSensorDescription(
        key="next_mode",
        translation_key="next_mode",
        device_class=SensorDeviceClass.ENUM,
        options=_MODES,
        value_fn=lambda state: state.next_mode.value if state.next_mode else None,
    ),
    AnodeModeSensorDescription(
        key="next_mode_time",
        translation_key="next_mode_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda state: state.next_change,
    ),
)

BATTERY_SENSORS: tuple[AnodeBatterySensorDescription, ...] = (
    AnodeBatterySensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda b: b.power_w,
    ),
    AnodeBatterySensorDescription(
        key="import_power",
        translation_key="battery_charge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda b: max(0.0, b.power_w),
    ),
    AnodeBatterySensorDescription(
        key="export_power",
        translation_key="battery_discharge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda b: abs(min(b.power_w, 0.0)),
    ),
    AnodeBatterySensorDescription(
        key="soc",
        translation_key="soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda b: round(b.soc_pct),
    ),
    AnodeBatterySensorDescription(
        key="capacity",
        translation_key="capacity",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_AMP_HOUR,
        value_fn=lambda b: b.capacity_ah,
    ),
    AnodeBatterySensorDescription(
        key="capacity_remaining",
        translation_key="capacity_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_AMP_HOUR,
        value_fn=lambda b: _round(b.capacity_remaining_ah, 2),
    ),
    AnodeBatterySensorDescription(
        key="energy_capacity",
        translation_key="energy_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value_fn=lambda b: _round(b.energy_capacity_wh, 1),
    ),
    AnodeBatterySensorDescription(
        key="energy_remaining",
        translation_key="energy_remaining",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value_fn=lambda b: _round(b.energy_remaining_wh, 1),
    ),
    AnodeBatterySensorDescription(
        key="nominal_voltage",
        translation_key="nominal_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.nominal_voltage_v,
    ),
    AnodeBatterySensorDescription(
        key="power_status",
        translation_key="power_status",
        value_fn=lambda b: b.power_status,
    ),
    AnodeBatterySensorDescription(
        key="charge_energy",
        translation_key="battery_charge_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda b: _round(b.charge_energy_kwh, 4),
    ),
    AnodeBatterySensorDescription(
        key="discharge_energy",
        translation_key="battery_discharge_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda b: _round(b.discharge_energy_kwh, 4),
    ),
)

BATTERY_BMS_SENSORS: tuple[AnodeBatterySensorDescription, ...] = (
    AnodeBatterySensorDescription(
        key="pack_voltage",
        translation_key="pack_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        exists_fn=lambda b: b.pack_voltage_v is not None,
        value_fn=lambda b: b.pack_voltage_v,
    ),
    AnodeBatterySensorDescription(
        key="max_temperature",
        translation_key="max_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        exists_fn=lambda b: bool(b.temperatures_c),
        value_fn=lambda b: b.max_temperature_c,
    ),
    AnodeBatterySensorDescription(
        key="min_temperature",
        translation_key="min_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        exists_fn=lambda b: bool(b.temperatures_c),
        value_fn=lambda b: b.min_temperature_c,
    ),
    AnodeBatterySensorDescription(
        key="cell_voltage_difference",
        translation_key="cell_voltage_difference",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_display_precision=0,
        exists_fn=lambda b: bool(b.cell_voltages_v),
        value_fn=lambda b: _round(b.cell_voltage_difference_mv, 1),
    ),
    AnodeBatterySensorDescription(
        key="max_cell_voltage",
        translation_key="max_cell_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        exists_fn=lambda b: bool(b.cell_voltages_v),
        value_fn=lambda b: _round(b.max_cell_voltage_v, 4),
    ),
    AnodeBatterySensorDescription(
        key="min_cell_voltage",
        translation_key="min_cell_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        exists_fn=lambda b: bool(b.cell_voltages_v),
        value_fn=lambda b: _round(b.min_cell_voltage_v, 4),
    ),
)

BATTERY_INDEXED_SENSORS: tuple[AnodeBatteryIndexedSensorDescription, ...] = (
    AnodeBatteryIndexedSensorDescription(
        key="temperature",
        translation_key="temperature_probe",
        placeholder="probe",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        values_fn=lambda b: b.temperatures_c,
    ),
    AnodeBatteryIndexedSensorDescription(
        key="cell_voltage",
        translation_key="cell_voltage",
        placeholder="cell",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        values_fn=lambda b: b.cell_voltages_v,
    ),
)

_FIRMWARE_VERSION = AnodeSubDeviceSensorDescription(
    key="version",
    translation_key="firmware_version",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda device: device.version,
)
_SUB_DEVICE_UPTIME = AnodeSubDeviceSensorDescription(
    key="uptime",
    translation_key="uptime",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.MILLISECONDS,
    suggested_unit_of_measurement=UnitOfTime.DAYS,
    state_class=SensorStateClass.TOTAL_INCREASING,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda device: device.uptime_ms,
)

BATTERY_STATUS_SENSORS: tuple[AnodeSubDeviceSensorDescription, ...] = (
    _FIRMWARE_VERSION,
    _SUB_DEVICE_UPTIME,
)

METER_SENSORS: tuple[AnodeMeterSensorDescription, ...] = (
    AnodeMeterSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda m: m.power_w,
    ),
    AnodeMeterSensorDescription(
        key="import_power",
        translation_key="import_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda m: max(0.0, m.power_w),
    ),
    AnodeMeterSensorDescription(
        key="export_power",
        translation_key="export_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda m: abs(min(m.power_w, 0.0)),
    ),
    AnodeMeterSensorDescription(
        key="import_energy",
        translation_key="import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda m: _round(m.import_energy_kwh, 4),
    ),
    AnodeMeterSensorDescription(
        key="export_energy",
        translation_key="export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda m: _round(m.export_energy_kwh, 4),
    ),
    AnodeMeterSensorDescription(
        key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.voltage_v,
    ),
    AnodeMeterSensorDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.current_a,
    ),
    AnodeMeterSensorDescription(
        key="power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.power_factor,
    ),
)

REPEATER_STATUS_SENSORS: tuple[AnodeSubDeviceSensorDescription, ...] = (
    _FIRMWARE_VERSION,
    _SUB_DEVICE_UPTIME,
)

METER_STATUS_SENSORS: tuple[AnodeSubDeviceSensorDescription, ...] = (
    AnodeSubDeviceSensorDescription(
        key="type",
        translation_key="meter_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.meter_type.value if device.meter_type else None,
    ),
    _FIRMWARE_VERSION,
    _SUB_DEVICE_UPTIME,
    AnodeSubDeviceSensorDescription(
        key="parent_meter",
        translation_key="parent_meter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.parent_meter,
        exists_fn=lambda device: device.parent_meter is not None,
    ),
)


def _has_batteries(status: HubStatus) -> bool:
    return bool(status.batteries)


def _has_grid_meter(status: HubStatus) -> bool:
    return bool(status.grid_meters)


def _battery_energy_totals(
    status: HubStatus, telemetry: Telemetry
) -> tuple[float, float] | None:
    """Sum capacity and remaining energy (Wh) over batteries reporting now."""
    total = remaining = 0.0
    found = False
    for battery_id in status.batteries:
        if not telemetry.is_current(battery_id):
            continue
        reading = telemetry.batteries[battery_id]
        capacity, left = reading.energy_capacity_wh, reading.energy_remaining_wh
        if capacity is None or left is None:
            continue
        total += capacity
        remaining += left
        found = True
    return (total, remaining) if found else None


def _total_energy_capacity(status: HubStatus, telemetry: Telemetry) -> float | None:
    totals = _battery_energy_totals(status, telemetry)
    return None if totals is None else round(totals[0], 1)


def _total_energy_remaining(status: HubStatus, telemetry: Telemetry) -> float | None:
    totals = _battery_energy_totals(status, telemetry)
    return None if totals is None else round(totals[1], 1)


def _average_soc(status: HubStatus, telemetry: Telemetry) -> float | None:
    """Capacity-weighted state of charge across batteries."""
    totals = _battery_energy_totals(status, telemetry)
    if totals is None or not totals[0]:
        return None
    return round(totals[1] / totals[0] * 100)


def _counter_total(
    readings: Mapping[str, BatteryReading | MeterReading],
    device_ids: Iterable[str],
    attr: str,
) -> float | None:
    """Sum one lifetime energy counter over several devices.

    Unknown if any device has never reported: leaving it out would make the
    total jump down now and up later. A device that has reported but has no
    counter yet (no energy history on the server) counts as zero.
    """
    values: list[float | None] = []
    for device_id in device_ids:
        if (reading := readings.get(device_id)) is None:
            return None
        values.append(getattr(reading, attr))
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _battery_counter_total(
    status: HubStatus, telemetry: Telemetry, attr: str
) -> float | None:
    """Sum a lifetime counter over every battery."""
    if not status.batteries or not telemetry.batteries_current:
        return None
    return _counter_total(telemetry.batteries, status.batteries, attr)


def _grid_counter_total(status: HubStatus, telemetry: Telemetry, attr: str) -> float | None:
    """Sum a lifetime counter over every grid meter."""
    if not status.grid_meters or not telemetry.meters_current:
        return None
    return _counter_total(telemetry.meters, (m.id for m in status.grid_meters), attr)


def _house_power(status: HubStatus, telemetry: Telemetry) -> float | None:
    """Grid power minus generation and battery power; positive is consumption."""
    if not status.grid_meters:
        return None
    total = 0.0
    for meter in status.grid_meters:
        if not telemetry.is_current(meter.id):
            return None
        total += telemetry.meters[meter.id].power_w
    for meter in status.generation_meters:
        if not telemetry.is_current(meter.id):
            return None
        total -= telemetry.meters[meter.id].power_w
    for battery_id in status.batteries:
        if telemetry.is_current(battery_id):
            total -= telemetry.batteries[battery_id].power_w
    return total


def _house_energy(status: HubStatus, telemetry: Telemetry) -> float | None:
    """Net house consumption (kWh) projected from lifetime counters.

    house = (grid import - grid export)
          + (generation export - generation import)
          + (battery discharge - battery charge)

    Needs the latest meter read, and battery read if there are batteries, to
    have succeeded. A device that has never reported makes the total unknown
    rather than treating its counters as zero.
    """
    grid = status.grid_meters
    if not grid or not telemetry.meters_current:
        return None
    if status.batteries and not telemetry.batteries_current:
        return None
    total = 0.0
    for meter in grid:
        if (reading := telemetry.meters.get(meter.id)) is None:
            return None
        total += (reading.import_energy_kwh or 0.0) - (reading.export_energy_kwh or 0.0)
    for meter in status.generation_meters:
        if (reading := telemetry.meters.get(meter.id)) is None:
            return None
        total += (reading.export_energy_kwh or 0.0) - (reading.import_energy_kwh or 0.0)
    for battery_id in status.batteries:
        if (battery := telemetry.batteries.get(battery_id)) is None:
            return None
        total += (battery.discharge_energy_kwh or 0.0) - (battery.charge_energy_kwh or 0.0)
    return total


HUB_TELEMETRY_SENSORS: tuple[AnodeHubTelemetrySensorDescription, ...] = (
    AnodeHubTelemetrySensorDescription(
        key="battery_energy_capacity",
        translation_key="total_battery_energy_capacity",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        exists_fn=_has_batteries,
        value_fn=_total_energy_capacity,
    ),
    AnodeHubTelemetrySensorDescription(
        key="battery_energy_remaining",
        translation_key="total_battery_energy_remaining",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        exists_fn=_has_batteries,
        value_fn=_total_energy_remaining,
    ),
    AnodeHubTelemetrySensorDescription(
        key="average_soc",
        translation_key="average_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        exists_fn=_has_batteries,
        value_fn=_average_soc,
    ),
    AnodeHubTelemetrySensorDescription(
        key="battery_cumulative_charge_energy",
        translation_key="total_battery_charge_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        exists_fn=_has_batteries,
        monotonic=True,
        value_fn=lambda s, t: _battery_counter_total(s, t, "charge_energy_kwh"),
    ),
    AnodeHubTelemetrySensorDescription(
        key="battery_cumulative_discharge_energy",
        translation_key="total_battery_discharge_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        exists_fn=_has_batteries,
        monotonic=True,
        value_fn=lambda s, t: _battery_counter_total(s, t, "discharge_energy_kwh"),
    ),
    AnodeHubTelemetrySensorDescription(
        key="grid_import_energy",
        translation_key="grid_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        exists_fn=_has_grid_meter,
        monotonic=True,
        value_fn=lambda s, t: _grid_counter_total(s, t, "import_energy_kwh"),
    ),
    AnodeHubTelemetrySensorDescription(
        key="grid_export_energy",
        translation_key="grid_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        exists_fn=_has_grid_meter,
        monotonic=True,
        value_fn=lambda s, t: _grid_counter_total(s, t, "export_energy_kwh"),
    ),
    AnodeHubTelemetrySensorDescription(
        key="house_power",
        translation_key="house_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        exists_fn=_has_grid_meter,
        value_fn=_house_power,
    ),
    AnodeHubTelemetrySensorDescription(
        key="house_energy",
        translation_key="house_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        exists_fn=_has_grid_meter,
        monotonic=True,
        value_fn=_house_energy,
    ),
)

_HUB_TELEMETRY_BY_KEY = {description.key: description for description in HUB_TELEMETRY_SENSORS}

# Energy since local midnight for each lifetime total: (today key, total key).
DAILY_SENSORS: tuple[AnodeHubTelemetrySensorDescription, ...] = tuple(
    replace(_HUB_TELEMETRY_BY_KEY[total], key=key, translation_key=key, monotonic=False)
    for key, total in (
        ("battery_charge_energy_today", "battery_cumulative_charge_energy"),
        ("battery_discharge_energy_today", "battery_cumulative_discharge_energy"),
        ("grid_import_energy_today", "grid_import_energy"),
        ("grid_export_energy_today", "grid_export_energy"),
        ("house_energy_today", "house_energy"),
    )
)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass
class DailyEnergyStoredData(ExtraStoredData):
    """What a daily energy sensor needs to carry on after a restart."""

    today: float | None
    baseline: float | None
    last_total: float | None
    day: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict for storage."""
        return asdict(self)


class AnodeDailyEnergySensor(AnodeEntity[AnodeTelemetryCoordinator], SensorEntity, RestoreEntity):
    """Energy since local midnight, from one of the hub's lifetime totals.

    Today's figure is the lifetime total minus a baseline: the last total seen
    before midnight. If the total falls during the day (a counter reset or a
    replaced device), the baseline moves so today's figure is kept and counting
    carries on. The baseline is restored after a restart, so energy used while
    Home Assistant was stopped still counts.
    """

    entity_description: AnodeHubTelemetrySensorDescription

    def __init__(
        self, runtime: AnodeRuntimeData, description: AnodeHubTelemetrySensorDescription
    ) -> None:
        super().__init__(runtime.telemetry, runtime.hub_id, description.key)
        self.entity_description = description
        self._status = runtime.status
        self._today: float | None = None
        self._baseline: float | None = None
        self._last_total: float | None = None
        self._day: date | None = None
        self._has_input = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore()
        self._recalculate()
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_midnight, hour=0, minute=0, second=0)
        )

    async def _async_restore(self) -> None:
        if (extra := await self.async_get_last_extra_data()) is not None:
            data = extra.as_dict()
            self._today = _float(data.get("today"))
            self._baseline = _float(data.get("baseline"))
            self._last_total = _float(data.get("last_total"))
            self._day = _date(data.get("day"))
            return
        # Releases before 0.2 kept the baseline in state attributes.
        if (last := await self.async_get_last_state()) is None:
            return
        self._today = _float(last.state)
        self._baseline = _float(last.attributes.get("baseline_kwh"))
        self._day = _date(last.attributes.get("last_reset_day"))
        if self._today is not None and self._baseline is not None:
            self._last_total = self._baseline + self._today

    def _start_day(self, day: date) -> None:
        self._day = day
        self._today = 0.0
        # Count from the last total seen, so energy used between that reading
        # and the first one today is not lost. None on a first run.
        self._baseline = self._last_total

    @callback
    def _async_midnight(self, now: datetime) -> None:
        self._start_day(dt_util.as_local(now).date())
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._recalculate()
        super()._handle_coordinator_update()

    def _recalculate(self) -> None:
        if self._day != (day := dt_util.now().date()):
            self._start_day(day)
        status, telemetry = self._status.data, self.coordinator.data
        total = (
            self.entity_description.value_fn(status, telemetry)
            if status is not None and telemetry is not None
            else None
        )
        self._has_input = total is not None
        if total is None:
            return
        total = round(total, 3)
        if self._baseline is None:
            self._baseline = total
        so_far = self._today or 0.0
        today = total - self._baseline
        if today < so_far:
            if so_far - today > ENERGY_DROP_TOLERANCE_KWH:
                # The lifetime total fell. Keep today's figure and count on
                # from the new total.
                self._baseline = total - so_far
            today = so_far
        self._today = round(today, 3)
        self._last_total = total

    @property
    def extra_restore_state_data(self) -> DailyEnergyStoredData:
        return DailyEnergyStoredData(
            today=self._today,
            baseline=self._baseline,
            last_total=self._last_total,
            day=self._day.isoformat() if self._day else None,
        )

    @property
    def available(self) -> bool:
        return super().available and self._has_input

    @property
    def native_value(self) -> float | None:
        return self._today


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode sensors."""
    runtime = entry.runtime_data

    def build() -> Iterator[Entity]:
        status = runtime.status.data
        for description in HUB_STATUS_SENSORS:
            yield AnodeHubStatusSensor(runtime.status, description)
        for description in MODE_SENSORS:
            yield AnodeModeSensor(runtime.mode, description)
        for description in HUB_TELEMETRY_SENSORS:
            if description.exists_fn(status):
                yield AnodeHubTelemetrySensor(runtime, description)
        for description in DAILY_SENSORS:
            if description.exists_fn(status):
                yield AnodeDailyEnergySensor(runtime, description)
        for battery in status.batteries.values():
            for description in BATTERY_SENSORS:
                yield AnodeBatterySensor(runtime.telemetry, battery.id, description)
            for description in BATTERY_STATUS_SENSORS:
                if description.exists_fn(battery):
                    yield AnodeSubDeviceSensor(runtime.status, battery.id, description)
        for meter in status.meters.values():
            for description in METER_SENSORS:
                yield AnodeMeterSensor(runtime.telemetry, meter.id, description)
            for description in METER_STATUS_SENSORS:
                if description.exists_fn(meter):
                    yield AnodeSubDeviceSensor(runtime.status, meter.id, description)
        for repeater in status.repeaters.values():
            for description in REPEATER_STATUS_SENSORS:
                if description.exists_fn(repeater):
                    yield AnodeSubDeviceSensor(runtime.status, repeater.id, description)

    async_setup_dynamic_entities(entry, async_add_entities, build)

    def build_bms() -> Iterator[Entity]:
        # BMS readings depend on battery firmware, so these entities are
        # created from telemetry once a battery reports them.
        status, telemetry = runtime.status.data, runtime.telemetry.data
        if telemetry is None:
            return
        for battery_id in status.batteries:
            if (reading := telemetry.batteries.get(battery_id)) is None:
                continue
            for description in BATTERY_BMS_SENSORS:
                if description.exists_fn(reading):
                    yield AnodeBatterySensor(runtime.telemetry, battery_id, description)
            for indexed in BATTERY_INDEXED_SENSORS:
                for index in range(len(indexed.values_fn(reading))):
                    yield AnodeBatteryIndexedSensor(runtime.telemetry, battery_id, indexed, index)

    async_setup_dynamic_entities(entry, async_add_entities, build_bms, runtime.telemetry)


class AnodeBatteryIndexedSensor(AnodeEntity[AnodeTelemetryCoordinator], SensorEntity):
    """One value from a battery list reading, such as cell 3's voltage."""

    entity_description: AnodeBatteryIndexedSensorDescription

    def __init__(
        self,
        coordinator: AnodeTelemetryCoordinator,
        battery_id: str,
        description: AnodeBatteryIndexedSensorDescription,
        index: int,
    ) -> None:
        super().__init__(coordinator, battery_id, f"{description.key}_{index + 1}")
        self.entity_description = description
        self._index = index
        self._attr_translation_placeholders = {description.placeholder: str(index + 1)}

    def _values(self) -> tuple[float, ...]:
        reading = self.coordinator.data.batteries.get(self._device_id)
        return self.entity_description.values_fn(reading) if reading else ()

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.is_current(self._device_id)
            and self._index < len(self._values())
        )

    @property
    def native_value(self) -> float | None:
        values = self._values()
        return values[self._index] if self._index < len(values) else None


class AnodeHubStatusSensor(AnodeEntity[AnodeStatusCoordinator], SensorEntity):
    """Hub sensor read from hub status."""

    entity_description: AnodeHubStatusSensorDescription

    def __init__(
        self, coordinator: AnodeStatusCoordinator, description: AnodeHubStatusSensorDescription
    ) -> None:
        super().__init__(coordinator, coordinator.hub_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)


class AnodeModeSensor(AnodeEntity[AnodeModeCoordinator], SensorEntity):
    """Hub sensor read from the mode and schedule."""

    entity_description: AnodeModeSensorDescription

    def __init__(
        self, coordinator: AnodeModeCoordinator, description: AnodeModeSensorDescription
    ) -> None:
        super().__init__(coordinator, coordinator.hub_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)


class AnodeSubDeviceSensor(AnodeEntity[AnodeStatusCoordinator], SensorEntity):
    """Battery, meter or repeater sensor read from hub status."""

    entity_description: AnodeSubDeviceSensorDescription

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        device_id: str,
        description: AnodeSubDeviceSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    def _device(self) -> SubDevice | None:
        return self.coordinator.data.sub_device(self._device_id)

    @property
    def available(self) -> bool:
        return super().available and self._device() is not None

    @property
    def native_value(self) -> StateType:
        device = self._device()
        return self.entity_description.value_fn(device) if device else None


class AnodeBatterySensor(AnodeEntity[AnodeTelemetryCoordinator], SensorEntity):
    """Battery sensor read from telemetry."""

    entity_description: AnodeBatterySensorDescription

    def __init__(
        self,
        coordinator: AnodeTelemetryCoordinator,
        battery_id: str,
        description: AnodeBatterySensorDescription,
    ) -> None:
        super().__init__(coordinator, battery_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.is_current(self._device_id)

    @property
    def native_value(self) -> StateType:
        reading = self.coordinator.data.batteries.get(self._device_id)
        return self.entity_description.value_fn(reading) if reading else None


class AnodeMeterSensor(AnodeEntity[AnodeTelemetryCoordinator], SensorEntity):
    """Meter sensor read from telemetry."""

    entity_description: AnodeMeterSensorDescription

    def __init__(
        self,
        coordinator: AnodeTelemetryCoordinator,
        meter_id: str,
        description: AnodeMeterSensorDescription,
    ) -> None:
        super().__init__(coordinator, meter_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.is_current(self._device_id)

    @property
    def native_value(self) -> StateType:
        reading = self.coordinator.data.meters.get(self._device_id)
        return self.entity_description.value_fn(reading) if reading else None


class AnodeHubTelemetrySensor(AnodeEntity[AnodeTelemetryCoordinator], RestoreSensor):
    """Hub sensor calculated from several devices.

    Unavailable whenever an input is missing, rather than calculating with a
    gap. Monotonic sensors restore their last value and hold it if the
    calculation dips, so an energy total never steps backwards.
    """

    entity_description: AnodeHubTelemetrySensorDescription

    def __init__(
        self, runtime: AnodeRuntimeData, description: AnodeHubTelemetrySensorDescription
    ) -> None:
        super().__init__(runtime.telemetry, runtime.hub_id, description.key)
        self.entity_description = description
        self._status = runtime.status
        self._value: float | None = None
        self._has_input = False
        self._holding = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.monotonic:
            last = await self.async_get_last_sensor_data()
            if last is not None and last.native_value is not None:
                try:
                    self._value = float(last.native_value)
                except (TypeError, ValueError):
                    self._value = None
        self._recalculate()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._recalculate()
        super()._handle_coordinator_update()

    def _recalculate(self) -> None:
        status, telemetry = self._status.data, self.coordinator.data
        raw = (
            self.entity_description.value_fn(status, telemetry)
            if status is not None and telemetry is not None
            else None
        )
        self._has_input = raw is not None
        if raw is None:
            return
        if not self.entity_description.monotonic:
            self._value = raw
            return
        raw = round(raw, 3)
        if self._value is None or raw >= self._value:
            self._value = raw
            self._holding = False
        elif self._value - raw > ENERGY_DROP_TOLERANCE_KWH and not self._holding:
            self._holding = True
            _LOGGER.warning(
                "%s calculated %.3f kWh, below its last value %.3f kWh; holding until it catches up",
                self.entity_id,
                raw,
                self._value,
            )

    @property
    def available(self) -> bool:
        return super().available and self._has_input

    @property
    def native_value(self) -> float | None:
        return self._value

"""Sensor platform for Anode integration."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_HUB_ID
from .coordinator import (
    AnodeStatusCoordinator,
    AnodeDeviceCoordinator,
    AnodeEnergyCoordinator,
    AnodeModeCoordinator,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode sensors."""
    hub_id = entry.data[CONF_HUB_ID]
    coordinators = hass.data[DOMAIN][entry.entry_id]

    status_coordinator: AnodeStatusCoordinator = coordinators["status_coordinator"]
    device_coordinator: AnodeDeviceCoordinator = coordinators["device_coordinator"]
    energy_coordinator: AnodeEnergyCoordinator = coordinators["energy_coordinator"]
    mode_coordinator: AnodeModeCoordinator = coordinators["mode_coordinator"]

    entities: list[SensorEntity] = []

    # Hub sensors
    entities.extend([
        AnodeHubModeSensor(mode_coordinator, hub_id, entry.entry_id),
        AnodeHubVersionSensor(status_coordinator, hub_id, entry.entry_id),
        AnodeHubUptimeSensor(status_coordinator, hub_id, entry.entry_id),
        AnodeHubNextModeSensor(mode_coordinator, hub_id, entry.entry_id),
        AnodeHubNextModeTimeSensor(mode_coordinator, hub_id, entry.entry_id),
    ])

    # Battery sensors
    status_data = status_coordinator.data
    battery_ids = [b["id"] for b in status_data.get("battery", [])]

    for battery in status_data.get("battery", []):
        battery_id = battery["id"]
        entities.extend([
            AnodeBatteryPowerSensor(device_coordinator, status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryImportPowerSensor(device_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryExportPowerSensor(device_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatterySOCSensor(device_coordinator, status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryCapacitySensor(device_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryCapacityRemainingSensor(device_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryNominalVoltageSensor(device_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryVersionSensor(status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryUptimeSensor(status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryPowerStatusSensor(device_coordinator, status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryChargeEnergySensor(device_coordinator, status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryDischargeEnergySensor(device_coordinator, status_coordinator, hub_id, battery_id, entry.entry_id),
        ])

    # Cumulative battery energy sensors (on hub device) + their daily-reset twins
    if battery_ids:
        battery_charge_total = AnodeBatteryCumulativeChargeEnergySensor(
            device_coordinator, status_coordinator, hub_id, entry.entry_id
        )
        battery_discharge_total = AnodeBatteryCumulativeDischargeEnergySensor(
            device_coordinator, status_coordinator, hub_id, entry.entry_id
        )
        entities.extend([
            battery_charge_total,
            battery_discharge_total,
            AnodeDailyResetEnergySensor(
                device_coordinator, battery_charge_total, hub_id,
                unique_suffix="battery_charge_energy_today",
                name_suffix="Battery Charge Energy Today",
                icon="mdi:battery-charging",
            ),
            AnodeDailyResetEnergySensor(
                device_coordinator, battery_discharge_total, hub_id,
                unique_suffix="battery_discharge_energy_today",
                name_suffix="Battery Discharge Energy Today",
                icon="mdi:battery-minus",
            ),
        ])

    # Meter sensors
    has_primary = False
    has_ext_inverter = False

    for meter in status_data.get("meter", []):
        meter_id = meter["id"]
        meter_type = meter.get("type")

        _LOGGER.debug("Found meter: id=%s, type=%s", meter_id, meter_type)

        if meter_type == "PRIMARY":
            has_primary = True
        elif meter_type == "EXT_INVERTER":
            has_ext_inverter = True

        entities.extend([
            AnodeMeterPowerSensor(device_coordinator, status_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterImportPowerSensor(device_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterExportPowerSensor(device_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterTypeSensor(status_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterVersionSensor(status_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterUptimeSensor(status_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterImportEnergySensor(device_coordinator, status_coordinator, hub_id, meter_id, entry.entry_id),
            AnodeMeterExportEnergySensor(device_coordinator, status_coordinator, hub_id, meter_id, entry.entry_id),
        ])
        # Add parent meter sensor if applicable
        if meter.get("parentMeter"):
            entities.append(
                AnodeMeterParentSensor(status_coordinator, hub_id, meter_id, entry.entry_id)
            )

    # Grid energy sensors on hub device (duplicating PRIMARY meter) + daily-reset twins
    if has_primary:
        grid_import_total = AnodeHubGridImportEnergySensor(
            device_coordinator, status_coordinator, hub_id, entry.entry_id
        )
        grid_export_total = AnodeHubGridExportEnergySensor(
            device_coordinator, status_coordinator, hub_id, entry.entry_id
        )
        entities.extend([
            grid_import_total,
            grid_export_total,
            AnodeDailyResetEnergySensor(
                device_coordinator, grid_import_total, hub_id,
                unique_suffix="grid_import_energy_today",
                name_suffix="Grid Import Energy Today",
                icon="mdi:transmission-tower-import",
            ),
            AnodeDailyResetEnergySensor(
                device_coordinator, grid_export_total, hub_id,
                unique_suffix="grid_export_energy_today",
                name_suffix="Grid Export Energy Today",
                icon="mdi:transmission-tower-export",
            ),
        ])

    # House power still requires both a grid meter and a generation source to
    # be meaningful (it subtracts ext-inverter power from primary).
    if has_primary and has_ext_inverter:
        entities.append(
            AnodeHousePowerSensor(device_coordinator, status_coordinator, hub_id, entry.entry_id)
        )

    # Net house energy: only a grid reference is required. A grid-only or
    # grid+battery hub (no solar) still gets a meaningful reading.
    has_grid = any(
        m.get("type") == "PRIMARY" or m.get("meterPurpose") == "primary"
        for m in status_data.get("meter", [])
    )
    if has_grid:
        _LOGGER.info("Adding net house energy sensor for hub %s", hub_id)
        house_energy_total = AnodeHouseEnergySensor(
            device_coordinator, status_coordinator, hub_id, entry.entry_id
        )
        entities.extend([
            house_energy_total,
            AnodeDailyResetEnergySensor(
                device_coordinator, house_energy_total, hub_id,
                unique_suffix="house_energy_today",
                name_suffix="House Energy Today",
                icon="mdi:home-lightning-bolt",
            ),
        ])

    async_add_entities(entities)


class AnodeHubModeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for hub operating mode."""

    def __init__(
        self,
        coordinator: AnodeModeCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_mode"
        self._attr_name = f"Anode Hub {hub_id} Mode"
        self._attr_icon = "mdi:flash"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("mode")


class AnodeHubVersionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for hub firmware version."""

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_version"
        self._attr_name = f"Anode Hub {hub_id} Version"
        self._attr_icon = "mdi:information"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("hub", {}).get("version")


class AnodeHubUptimeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for hub uptime."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_uptime"
        self._attr_name = f"Anode Hub {hub_id} Uptime"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("hub", {}).get("uptime")


class AnodeHubNextModeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for next scheduled mode."""

    def __init__(
        self,
        coordinator: AnodeModeCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_next_mode"
        self._attr_name = f"Anode Hub {hub_id} Next Mode"
        self._attr_icon = "mdi:clock-outline"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("next_mode")


class AnodeHubNextModeTimeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for next scheduled mode time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: AnodeModeCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_next_mode_time"
        self._attr_name = f"Anode Hub {hub_id} Next Mode Time"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("next_time")


class AnodeBatteryPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{battery_id}_power"
        self._attr_name = f"Anode Battery {battery_id} Power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
            name=f"Anode {battery_id}",
            manufacturer="Anode",
            model="Battery",
            via_device=(DOMAIN, hub_id),
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "power" in battery_data:
            return battery_data["power"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "power" in battery_data:
            unit = battery_data["power"].get("unit", "").upper()
            # Map API units to HA units
            if unit == "W":
                return UnitOfPower.WATT
            elif unit == "KW":
                return UnitOfPower.KILO_WATT
            return unit
        return UnitOfPower.WATT


class AnodeBatteryImportPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery import (charging) power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_import_power"
        self._attr_name = f"Anode Battery {battery_id} Import Power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "power" in battery_data:
            value = battery_data["power"].get("value")
            if value is not None:
                return max(value, 0)
        return None


class AnodeBatteryExportPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery export (discharging) power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_export_power"
        self._attr_name = f"Anode Battery {battery_id} Export Power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "power" in battery_data:
            value = battery_data["power"].get("value")
            if value is not None:
                return abs(min(value, 0))
        return None


class AnodeBatterySOCSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery state of charge."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._hub_id = hub_id
        self._attr_unique_id = f"{battery_id}_soc"
        self._attr_name = f"Anode Battery {battery_id} State of Charge"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "soc" in battery_data:
            return battery_data["soc"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "soc" in battery_data:
            unit = battery_data["soc"].get("unit", "%")
            return unit
        return PERCENTAGE


class AnodeBatteryCapacitySensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery total capacity in Ah."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "Ah"
    _attr_icon = "mdi:battery-heart-variant"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_capacity"
        self._attr_name = f"Anode Battery {battery_id} Capacity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        """Return the total capacity in Ah."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "capacity" in battery_data:
            return battery_data["capacity"].get("value")
        return None


class AnodeBatteryCapacityRemainingSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery remaining capacity in Ah (capacity * SOC)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "Ah"
    _attr_icon = "mdi:battery-clock"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_capacity_remaining"
        self._attr_name = f"Anode Battery {battery_id} Capacity Remaining"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        """Return the remaining capacity in Ah."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if not battery_data:
            return None
        capacity = battery_data.get("capacity", {}).get("value")
        soc = battery_data.get("soc", {}).get("value")
        if capacity is not None and soc is not None:
            return round(capacity * soc / 100, 2)
        return None


class AnodeBatteryNominalVoltageSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery nominal voltage."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_nominal_voltage"
        self._attr_name = f"Anode Battery {battery_id} Nominal Voltage"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        """Return the nominal voltage."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "capacity" in battery_data:
            return battery_data["capacity"].get("nominalVoltage")
        return None


class AnodeBatteryVersionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery firmware version."""

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_version"
        self._attr_name = f"Anode Battery {battery_id} Version"
        self._attr_icon = "mdi:information"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        for battery in self.coordinator.data.get("battery", []):
            if battery["id"] == self._battery_id:
                return battery.get("version")
        return None


class AnodeBatteryUptimeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery uptime."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_uptime"
        self._attr_name = f"Anode Battery {battery_id} Uptime"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        for battery in self.coordinator.data.get("battery", []):
            if battery["id"] == self._battery_id:
                return battery.get("uptime")
        return None


class AnodeBatteryPowerStatusSensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery power status (CHARGING, DISCHARGING, etc.)."""

    _attr_icon = "mdi:battery-sync"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{battery_id}_power_status"
        self._attr_name = f"Anode Battery {battery_id} Power Status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the power status."""
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data:
            return battery_data.get("powerStatus")
        return None


class AnodeMeterPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{meter_id}_power"
        self._attr_name = f"Anode Meter {meter_id} Power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
            name=f"Anode Meter {meter_id}",
            manufacturer="Anode",
            model="Meter",
            via_device=(DOMAIN, hub_id),
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "power" in meter_data:
            return meter_data["power"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "power" in meter_data:
            unit = meter_data["power"].get("unit", "").upper()
            # Map API units to HA units
            if unit == "W":
                return UnitOfPower.WATT
            elif unit == "KW":
                return UnitOfPower.KILO_WATT
            return unit
        return UnitOfPower.WATT


class AnodeMeterImportPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter import power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_import_power"
        self._attr_name = f"Anode Meter {meter_id} Import Power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def native_value(self) -> float | None:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "power" in meter_data:
            value = meter_data["power"].get("value")
            if value is not None:
                return max(value, 0)
        return None


class AnodeMeterExportPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter export power."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_export_power"
        self._attr_name = f"Anode Meter {meter_id} Export Power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def native_value(self) -> float | None:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "power" in meter_data:
            value = meter_data["power"].get("value")
            if value is not None:
                return abs(min(value, 0))
        return None


class AnodeMeterTypeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter type."""

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_type"
        self._attr_name = f"Anode Meter {meter_id} Type"
        self._attr_icon = "mdi:label"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        for meter in self.coordinator.data.get("meter", []):
            if meter["id"] == self._meter_id:
                return meter.get("type")
        return None


class AnodeMeterVersionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter firmware version."""

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_version"
        self._attr_name = f"Anode Meter {meter_id} Version"
        self._attr_icon = "mdi:information"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        for meter in self.coordinator.data.get("meter", []):
            if meter["id"] == self._meter_id:
                return meter.get("version")
        return None


class AnodeMeterUptimeSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter uptime."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_uptime"
        self._attr_name = f"Anode Meter {meter_id} Uptime"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        for meter in self.coordinator.data.get("meter", []):
            if meter["id"] == self._meter_id:
                return meter.get("uptime")
        return None


class AnodeMeterParentSensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter parent meter."""

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{meter_id}_parent_meter"
        self._attr_name = f"Anode Meter {meter_id} Parent Meter"
        self._attr_icon = "mdi:link"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        for meter in self.coordinator.data.get("meter", []):
            if meter["id"] == self._meter_id:
                return meter.get("parentMeter")
        return None


class AnodeHousePowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for house power (PRIMARY - EXT_INVERTER)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_house_power"
        self._attr_name = f"Anode Hub {hub_id} House Power"
        self._attr_icon = "mdi:home-lightning-bolt"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> float | None:
        """Return house power (PRIMARY - EXT_INVERTER - sum(batteries)).

        Negative values = generation
        Positive values = load
        """
        # Get meter IDs from status data
        primary_id = None
        ext_inverter_id = None

        for meter in self._status_coordinator.data.get("meter", []):
            meter_type = meter.get("type")
            if meter_type == "PRIMARY":
                primary_id = meter["id"]
            elif meter_type == "EXT_INVERTER":
                ext_inverter_id = meter["id"]

        if not primary_id or not ext_inverter_id:
            return None

        # Get power values from device coordinator
        meters_data = self.coordinator.data.get("meters", {})
        primary_data = meters_data.get(primary_id)
        ext_inverter_data = meters_data.get(ext_inverter_id)

        if not primary_data or not ext_inverter_data:
            return None

        primary_power = primary_data.get("power", {}).get("value")
        ext_inverter_power = ext_inverter_data.get("power", {}).get("value")

        if primary_power is None or ext_inverter_power is None:
            return None

        # Get battery IDs and sum their power
        battery_ids = [battery["id"] for battery in self._status_coordinator.data.get("battery", [])]
        batteries_data = self.coordinator.data.get("batteries", {})

        total_battery_power = 0.0
        for battery_id in battery_ids:
            battery_data = batteries_data.get(battery_id)
            if battery_data and "power" in battery_data:
                battery_power = battery_data["power"].get("value")
                if battery_power is not None:
                    total_battery_power += battery_power

        # Calculate house power: PRIMARY - EXT_INVERTER - sum(batteries)
        # When battery discharges (negative), it adds to house power
        return primary_power - ext_inverter_power - total_battery_power

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        # Use the unit from PRIMARY meter
        primary_id = None
        for meter in self._status_coordinator.data.get("meter", []):
            if meter.get("type") == "PRIMARY":
                primary_id = meter["id"]
                break

        if not primary_id:
            return UnitOfPower.WATT

        meters_data = self.coordinator.data.get("meters", {})
        primary_data = meters_data.get(primary_id)

        if primary_data and "power" in primary_data:
            unit = primary_data["power"].get("unit", "").upper()
            if unit == "W":
                return UnitOfPower.WATT
            elif unit == "KW":
                return UnitOfPower.KILO_WATT
            return unit

        return UnitOfPower.WATT


# ---------------------------------------------------------------------------
# Battery energy sensors (hardware counters from device coordinator)
# ---------------------------------------------------------------------------


class _CumulativeEnergySensorBase(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Base for energy sensors that accumulate deltas into a running total."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, **kwargs):
        super().__init__(coordinator)
        self._cumulative_kwh: float = 0.0
        self._restored: bool = False

    async def async_added_to_hass(self) -> None:
        """Restore cumulative total from previous HA session."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._cumulative_kwh = float(last_state.state)
            except (ValueError, TypeError):
                self._cumulative_kwh = 0.0
        self._restored = True
        # Accumulate the initial coordinator data if already available
        delta = self._get_delta_kwh()
        if delta is not None and delta > 0:
            self._cumulative_kwh += delta
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Accumulate delta on each coordinator update, then write state."""
        if self._restored:
            delta = self._get_delta_kwh()
            if delta is not None and delta > 0:
                self._cumulative_kwh += delta
        super()._handle_coordinator_update()

    def _get_delta_kwh(self) -> float | None:
        """Subclasses return the current delta in kWh from coordinator data."""
        raise NotImplementedError

    @property
    def native_value(self) -> float | None:
        if not self._restored:
            return None
        return round(self._cumulative_kwh, 3)


class AnodeBatteryChargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery charge energy (hardware counter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{battery_id}_charge_energy"
        self._attr_name = f"Anode Battery {battery_id} Charge Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "importEnergy" in battery_data:
            return battery_data["importEnergy"].get("value", 0) / 10000
        return None


class AnodeBatteryDischargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery discharge energy (hardware counter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{battery_id}_discharge_energy"
        self._attr_name = f"Anode Battery {battery_id} Discharge Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        battery_data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if battery_data and "exportEnergy" in battery_data:
            return battery_data["exportEnergy"].get("value", 0) / 10000
        return None


class AnodeBatteryCumulativeChargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for total charge energy across all batteries (hardware counter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_battery_cumulative_charge_energy"
        self._attr_name = f"Anode Hub {hub_id} Battery Cumulative Charge Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> float | None:
        batteries = self.coordinator.data.get("batteries", {})
        if not batteries:
            return None
        total = sum(
            b.get("importEnergy", {}).get("value", 0)
            for b in batteries.values()
        )
        return total / 10000


class AnodeBatteryCumulativeDischargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for total discharge energy across all batteries (hardware counter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_battery_cumulative_discharge_energy"
        self._attr_name = f"Anode Hub {hub_id} Battery Cumulative Discharge Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> float | None:
        batteries = self.coordinator.data.get("batteries", {})
        if not batteries:
            return None
        total = sum(
            b.get("exportEnergy", {}).get("value", 0)
            for b in batteries.values()
        )
        return total / 10000


# ---------------------------------------------------------------------------
# Meter energy sensors (hardware counters from device coordinator)
# ---------------------------------------------------------------------------

class AnodeMeterImportEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter import energy (hardware counter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{meter_id}_import_energy"
        self._attr_name = f"Anode Meter {meter_id} Import Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "importEnergy" in meter_data:
            # Raw meter API returns dWh; convert to kWh
            return meter_data["importEnergy"].get("value", 0) / 10000
        return None


class AnodeMeterExportEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for meter export energy (hardware counter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{meter_id}_export_energy"
        self._attr_name = f"Anode Meter {meter_id} Export Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "exportEnergy" in meter_data:
            # Raw meter API returns dWh; convert to kWh
            return meter_data["exportEnergy"].get("value", 0) / 10000
        return None


# ---------------------------------------------------------------------------
# Grid meter energy sensors on hub device (duplicate of PRIMARY meter)
# ---------------------------------------------------------------------------

class AnodeHubGridImportEnergySensor(CoordinatorEntity, SensorEntity):
    """Grid import energy sensor on hub device (mirrors PRIMARY meter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_grid_import_energy"
        self._attr_name = f"Anode Hub {hub_id} Grid Import Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    def _get_primary_meter_data(self) -> dict | None:
        for meter in self._status_coordinator.data.get("meter", []):
            if meter.get("type") == "PRIMARY":
                return self.coordinator.data.get("meters", {}).get(meter["id"])
        return None

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        data = self._get_primary_meter_data()
        if data and "importEnergy" in data:
            # Raw meter API returns dWh; convert to kWh
            return data["importEnergy"].get("value", 0) / 10000
        return None


class AnodeHubGridExportEnergySensor(CoordinatorEntity, SensorEntity):
    """Grid export energy sensor on hub device (mirrors PRIMARY meter)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(
        self,
        coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_grid_export_energy"
        self._attr_name = f"Anode Hub {hub_id} Grid Export Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    def _get_primary_meter_data(self) -> dict | None:
        for meter in self._status_coordinator.data.get("meter", []):
            if meter.get("type") == "PRIMARY":
                return self.coordinator.data.get("meters", {}).get(meter["id"])
        return None

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        data = self._get_primary_meter_data()
        if data and "exportEnergy" in data:
            # Raw meter API returns dWh; convert to kWh
            return data["exportEnergy"].get("value", 0) / 10000
        return None


# ---------------------------------------------------------------------------
# Derived house energy sensor (projection of hardware counters)
# ---------------------------------------------------------------------------


def _is_grid_meter(meter: dict) -> bool:
    """A grid reference meter (PRIMARY type or meterPurpose == primary)."""
    return meter.get("type") == "PRIMARY" or meter.get("meterPurpose") == "primary"


def _is_generation_meter(meter: dict) -> bool:
    """A generation source meter (solar inverter, PV, etc.)."""
    return meter.get("type") == "EXT_INVERTER" or meter.get("meterPurpose") == "solar"


def _counter_kwh(d: dict | None, key: str) -> float:
    """Read a hardware energy counter (dWh) and return kWh. 0.0 on missing."""
    if not d:
        return 0.0
    entry = d.get(key)
    if not isinstance(entry, dict):
        return 0.0
    value = entry.get("value")
    if value is None:
        return 0.0
    try:
        return float(value) / 10000.0
    except (TypeError, ValueError):
        return 0.0


def _calc_house_energy_total(
    device_coordinator: AnodeDeviceCoordinator,
    status_coordinator: AnodeStatusCoordinator,
) -> float | None:
    """Project the net house-load total directly from monotonic hardware counters.

    house_total = (grid_import - grid_export)
                + Σ(gen_export - gen_import)        over generation meters
                + Σ(batt_discharge - batt_charge)   over all batteries

    All inputs come straight from the hub's per-device counters via
    `AnodeDeviceCoordinator`, so there is no windowed accumulation, no clamping,
    and no drift against the underlying meters.
    """
    meters_meta = status_coordinator.data.get("meter", []) or []
    meters_data = device_coordinator.data.get("meters", {}) or {}
    batteries_data = device_coordinator.data.get("batteries", {}) or {}

    grid_ids = [m["id"] for m in meters_meta if _is_grid_meter(m)]
    gen_ids = [m["id"] for m in meters_meta if _is_generation_meter(m)]

    if not grid_ids:
        # Without a grid reference we can't meaningfully close the loop.
        return None

    total = 0.0
    for mid in grid_ids:
        d = meters_data.get(mid)
        total += _counter_kwh(d, "importEnergy") - _counter_kwh(d, "exportEnergy")
    for mid in gen_ids:
        d = meters_data.get(mid)
        total += _counter_kwh(d, "exportEnergy") - _counter_kwh(d, "importEnergy")
    for d in batteries_data.values():
        total += _counter_kwh(d, "exportEnergy") - _counter_kwh(d, "importEnergy")

    return total


class AnodeHouseEnergySensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Net house energy, derived from hardware counters with a high-watermark.

    The underlying counters aren't all polled atomically, so their sum can
    briefly dip a few Wh when one device's counter refreshes before another.
    We publish max(current_sum, persisted_peak) so the exposed value stays
    monotonic and the HA Energy dashboard is happy with `TOTAL_INCREASING`.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(
        self,
        device_coordinator: AnodeDeviceCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(device_coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._peak_kwh: float | None = None
        self._attr_unique_id = f"{hub_id}_house_energy"
        self._attr_name = f"Anode Hub {hub_id} House Energy"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub_id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._peak_kwh = float(last_state.state)
            except (TypeError, ValueError):
                self._peak_kwh = None

    @property
    def native_value(self) -> float | None:
        current = _calc_house_energy_total(self.coordinator, self._status_coordinator)
        if current is None:
            # Fall back to the last-known peak rather than going unavailable,
            # so Energy dashboard statistics don't see a gap on transient
            # coordinator misses.
            return round(self._peak_kwh, 3) if self._peak_kwh is not None else None
        if self._peak_kwh is None or current > self._peak_kwh:
            self._peak_kwh = current
        return round(self._peak_kwh, 3)


# ---------------------------------------------------------------------------
# Daily-reset ("today") wrapper sensors
# ---------------------------------------------------------------------------


class AnodeDailyResetEnergySensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Wrap a TOTAL_INCREASING energy sensor and expose a daily-reset version.

    Keeps a baseline snapshot of the source sensor's cumulative value taken at
    the most recent local midnight. native_value = source_total - baseline
    (clamped ≥0). Baseline and reset day are persisted as state attributes so
    the "today" figure survives HA restarts mid-day.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator,
        source_sensor: SensorEntity,
        hub_id: str,
        unique_suffix: str,
        name_suffix: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_sensor = source_sensor
        self._hub_id = hub_id
        self._baseline_kwh: float | None = None
        self._last_reset_day: str | None = None
        self._attr_unique_id = f"{hub_id}_{unique_suffix}"
        self._attr_name = f"Anode Hub {hub_id} {name_suffix}"
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub_id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.attributes:
            baseline = last_state.attributes.get("baseline_kwh")
            last_day = last_state.attributes.get("last_reset_day")
            if isinstance(baseline, (int, float)):
                self._baseline_kwh = float(baseline)
            if isinstance(last_day, str):
                self._last_reset_day = last_day

        # If we've rolled past midnight while HA was down (or this is the
        # very first run), rebaseline now against the current source value.
        self._maybe_rebaseline()

        # Schedule a reset at every local midnight.
        self.async_on_remove(
            async_track_time_change(
                self.hass,
                self._midnight_reset,
                hour=0,
                minute=0,
                second=0,
            )
        )

    @callback
    def _midnight_reset(self, _now) -> None:
        total = self._current_total()
        if total is None:
            return
        self._baseline_kwh = total
        self._last_reset_day = dt_util.now().date().isoformat()
        self.async_write_ha_state()

    def _current_total(self) -> float | None:
        try:
            total = self._source_sensor.native_value
        except Exception:  # noqa: BLE001
            return None
        if total is None:
            return None
        try:
            return float(total)
        except (TypeError, ValueError):
            return None

    def _maybe_rebaseline(self) -> None:
        total = self._current_total()
        if total is None:
            return
        today = dt_util.now().date().isoformat()
        if self._last_reset_day != today or self._baseline_kwh is None:
            self._baseline_kwh = total
            self._last_reset_day = today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "baseline_kwh": self._baseline_kwh,
            "last_reset_day": self._last_reset_day,
        }

    @property
    def native_value(self) -> float | None:
        total = self._current_total()
        if total is None or self._baseline_kwh is None:
            return None
        return max(round(total - self._baseline_kwh, 3), 0.0)

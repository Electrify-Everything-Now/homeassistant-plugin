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
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

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
            AnodeBatterySOCSensor(device_coordinator, status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryVersionSensor(status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryUptimeSensor(status_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryChargeEnergySensor(energy_coordinator, hub_id, battery_id, entry.entry_id),
            AnodeBatteryDischargeEnergySensor(energy_coordinator, hub_id, battery_id, entry.entry_id),
        ])

    # Cumulative battery energy sensors (on hub device)
    if battery_ids:
        entities.extend([
            AnodeBatteryCumulativeChargeEnergySensor(energy_coordinator, hub_id, entry.entry_id),
            AnodeBatteryCumulativeDischargeEnergySensor(energy_coordinator, hub_id, entry.entry_id),
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

    # Add grid energy sensors on hub device (duplicating PRIMARY meter)
    if has_primary:
        entities.extend([
            AnodeHubGridImportEnergySensor(device_coordinator, status_coordinator, hub_id, entry.entry_id),
            AnodeHubGridExportEnergySensor(device_coordinator, status_coordinator, hub_id, entry.entry_id),
        ])

    # Add house sensors if both PRIMARY and EXT_INVERTER are present
    _LOGGER.info("House sensor check: has_primary=%s, has_ext_inverter=%s", has_primary, has_ext_inverter)
    if has_primary and has_ext_inverter:
        _LOGGER.info("Adding house power and energy sensors for hub %s", hub_id)
        entities.extend([
            AnodeHousePowerSensor(device_coordinator, status_coordinator, hub_id, entry.entry_id),
            AnodeHouseEnergyConsumedSensor(energy_coordinator, status_coordinator, hub_id, entry.entry_id),
            AnodeHouseEnergyGeneratedSensor(energy_coordinator, status_coordinator, hub_id, entry.entry_id),
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
    _attr_suggested_unit_of_measurement = UnitOfTime.DAYS
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
        self._attr_name = f"Anode {battery_id} Power"
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
        self._attr_name = f"Anode {battery_id} State of Charge"
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
        self._attr_name = f"Anode {battery_id} Version"
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
        self._attr_name = f"Anode {battery_id} Uptime"
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
# Battery energy sensors (via telemetry API, 1-hour rolling window)
# ---------------------------------------------------------------------------

class AnodeBatteryChargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery charge energy (import Wh over last hour)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        coordinator: AnodeEnergyCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_charge_energy"
        self._attr_name = f"Anode {battery_id} Charge Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if data is None:
            return None
        wh = data.get("import_wh")
        if wh is None:
            return None
        return round(wh / 1000, 3)


class AnodeBatteryDischargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for battery discharge energy (export Wh over last hour)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        coordinator: AnodeEnergyCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_discharge_energy"
        self._attr_name = f"Anode {battery_id} Discharge Energy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get("batteries", {}).get(self._battery_id)
        if data is None:
            return None
        wh = data.get("export_wh")
        if wh is None:
            return None
        return round(wh / 1000, 3)


class AnodeBatteryCumulativeChargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for total charge energy across all batteries (last hour)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        coordinator: AnodeEnergyCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
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
        total_wh = sum(d.get("import_wh", 0.0) for d in batteries.values())
        return round(total_wh / 1000, 3)


class AnodeBatteryCumulativeDischargeEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for total discharge energy across all batteries (last hour)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        coordinator: AnodeEnergyCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
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
        total_wh = sum(d.get("export_wh", 0.0) for d in batteries.values())
        return round(total_wh / 1000, 3)


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

    @property
    def native_value(self) -> float | None:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "importEnergy" in meter_data:
            return meter_data["importEnergy"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "importEnergy" in meter_data:
            unit = meter_data["importEnergy"].get("unit", "kWh").lower()
            if unit == "kwh":
                return UnitOfEnergy.KILO_WATT_HOUR
            if unit == "wh":
                return UnitOfEnergy.WATT_HOUR
        return UnitOfEnergy.KILO_WATT_HOUR


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

    @property
    def native_value(self) -> float | None:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "exportEnergy" in meter_data:
            return meter_data["exportEnergy"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        meter_data = self.coordinator.data.get("meters", {}).get(self._meter_id)
        if meter_data and "exportEnergy" in meter_data:
            unit = meter_data["exportEnergy"].get("unit", "kWh").lower()
            if unit == "kwh":
                return UnitOfEnergy.KILO_WATT_HOUR
            if unit == "wh":
                return UnitOfEnergy.WATT_HOUR
        return UnitOfEnergy.KILO_WATT_HOUR


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

    @property
    def native_value(self) -> float | None:
        data = self._get_primary_meter_data()
        if data and "importEnergy" in data:
            return data["importEnergy"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        data = self._get_primary_meter_data()
        if data and "importEnergy" in data:
            unit = data["importEnergy"].get("unit", "kWh").lower()
            if unit == "kwh":
                return UnitOfEnergy.KILO_WATT_HOUR
            if unit == "wh":
                return UnitOfEnergy.WATT_HOUR
        return UnitOfEnergy.KILO_WATT_HOUR


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

    @property
    def native_value(self) -> float | None:
        data = self._get_primary_meter_data()
        if data and "exportEnergy" in data:
            return data["exportEnergy"].get("value")
        return None

    @property
    def native_unit_of_measurement(self) -> str:
        data = self._get_primary_meter_data()
        if data and "exportEnergy" in data:
            unit = data["exportEnergy"].get("unit", "kWh").lower()
            if unit == "kwh":
                return UnitOfEnergy.KILO_WATT_HOUR
            if unit == "wh":
                return UnitOfEnergy.WATT_HOUR
        return UnitOfEnergy.KILO_WATT_HOUR


# ---------------------------------------------------------------------------
# Derived house energy sensors (1-hour rolling window via energy coordinator)
# ---------------------------------------------------------------------------

class AnodeHouseEnergyConsumedSensor(CoordinatorEntity, SensorEntity):
    """Derived house consumed energy over the last hour."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(
        self,
        coordinator: AnodeEnergyCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_house_energy_consumed"
        self._attr_name = f"Anode Hub {hub_id} House Energy Consumed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    def _calc_house_energy(self) -> tuple[float | None, float | None]:
        """Return (consumed_kwh, generated_kwh) or (None, None)."""
        primary_id = None
        ext_inverter_id = None
        for meter in self._status_coordinator.data.get("meter", []):
            t = meter.get("type")
            if t == "PRIMARY":
                primary_id = meter["id"]
            elif t == "EXT_INVERTER":
                ext_inverter_id = meter["id"]

        if not primary_id or not ext_inverter_id:
            return None, None

        meters = self.coordinator.data.get("meters", {})
        primary = meters.get(primary_id)
        ext_inv = meters.get(ext_inverter_id)
        if not primary or not ext_inv:
            return None, None

        primary_import = primary.get("import_wh", 0.0)
        primary_export = primary.get("export_wh", 0.0)
        ext_import = ext_inv.get("import_wh", 0.0)
        ext_export = ext_inv.get("export_wh", 0.0)

        battery_ids = [b["id"] for b in self._status_coordinator.data.get("battery", [])]
        batteries = self.coordinator.data.get("batteries", {})
        batt_charge = sum(batteries.get(bid, {}).get("import_wh", 0.0) for bid in battery_ids)
        batt_discharge = sum(batteries.get(bid, {}).get("export_wh", 0.0) for bid in battery_ids)

        # House consumed = grid import + battery discharge - ext_inverter import
        consumed_wh = primary_import + batt_discharge - ext_import - batt_charge
        # House generated (surplus to grid) = ext_inverter export + battery charge - grid export - battery discharge
        generated_wh = ext_export + batt_charge - primary_export - batt_discharge

        return (
            round(max(consumed_wh, 0.0) / 1000, 3),
            round(max(generated_wh, 0.0) / 1000, 3),
        )

    @property
    def native_value(self) -> float | None:
        consumed, _ = self._calc_house_energy()
        return consumed


class AnodeHouseEnergyGeneratedSensor(CoordinatorEntity, SensorEntity):
    """Derived house generated/exported energy over the last hour."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: AnodeEnergyCoordinator,
        status_coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._status_coordinator = status_coordinator
        self._attr_unique_id = f"{hub_id}_house_energy_generated"
        self._attr_name = f"Anode Hub {hub_id} House Energy Generated"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    def _calc_house_energy(self) -> tuple[float | None, float | None]:
        """Return (consumed_kwh, generated_kwh) or (None, None)."""
        primary_id = None
        ext_inverter_id = None
        for meter in self._status_coordinator.data.get("meter", []):
            t = meter.get("type")
            if t == "PRIMARY":
                primary_id = meter["id"]
            elif t == "EXT_INVERTER":
                ext_inverter_id = meter["id"]

        if not primary_id or not ext_inverter_id:
            return None, None

        meters = self.coordinator.data.get("meters", {})
        primary = meters.get(primary_id)
        ext_inv = meters.get(ext_inverter_id)
        if not primary or not ext_inv:
            return None, None

        primary_import = primary.get("import_wh", 0.0)
        primary_export = primary.get("export_wh", 0.0)
        ext_import = ext_inv.get("import_wh", 0.0)
        ext_export = ext_inv.get("export_wh", 0.0)

        battery_ids = [b["id"] for b in self._status_coordinator.data.get("battery", [])]
        batteries = self.coordinator.data.get("batteries", {})
        batt_charge = sum(batteries.get(bid, {}).get("import_wh", 0.0) for bid in battery_ids)
        batt_discharge = sum(batteries.get(bid, {}).get("export_wh", 0.0) for bid in battery_ids)

        consumed_wh = primary_import + batt_discharge - ext_import - batt_charge
        generated_wh = ext_export + batt_charge - primary_export - batt_discharge

        return (
            round(max(consumed_wh, 0.0) / 1000, 3),
            round(max(generated_wh, 0.0) / 1000, 3),
        )

    @property
    def native_value(self) -> float | None:
        _, generated = self._calc_house_energy()
        return generated

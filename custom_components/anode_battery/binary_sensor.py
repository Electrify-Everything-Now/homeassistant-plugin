"""Binary sensor platform for Anode integration."""
from __future__ import annotations

from datetime import time
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_HUB_ID
from .coordinator import AnodeStatusCoordinator, AnodeModeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode binary sensors."""
    hub_id = entry.data[CONF_HUB_ID]
    coordinators = hass.data[DOMAIN][entry.entry_id]

    status_coordinator: AnodeStatusCoordinator = coordinators["status_coordinator"]
    mode_coordinator: AnodeModeCoordinator = coordinators["mode_coordinator"]

    entities: list[BinarySensorEntity] = []

    # Hub online sensor (based on successful API response)
    entities.append(AnodeHubOnlineSensor(status_coordinator, hub_id, entry.entry_id))

    # Hub override sensor
    entities.append(AnodeHubOverrideSensor(mode_coordinator, hub_id, entry.entry_id))

    # Battery online sensors
    status_data = status_coordinator.data
    for battery in status_data.get("battery", []):
        battery_id = battery["id"]
        entities.append(
            AnodeBatteryOnlineSensor(status_coordinator, hub_id, battery_id, entry.entry_id)
        )

    # Meter online sensors
    for meter in status_data.get("meter", []):
        meter_id = meter["id"]
        entities.append(
            AnodeMeterOnlineSensor(status_coordinator, hub_id, meter_id, entry.entry_id)
        )

    async_add_entities(entities)


class AnodeHubOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for hub online status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_online"
        self._attr_name = f"Anode Hub {hub_id} Online"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return true if hub is online."""
        # Hub is online if we have status data and status is true
        return self.coordinator.data.get("status", False)


class AnodeHubOverrideSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for hub override status."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: AnodeModeCoordinator,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_override_active"
        self._attr_name = f"Anode Hub {hub_id} Override Active"
        self._attr_icon = "mdi:alert-circle"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return true if hub is in override mode."""
        current_mode = self.coordinator.data.get("mode")
        schedule = self.coordinator.data.get("schedule", [])

        if not current_mode:
            return False

        # Get the expected mode based on schedule
        expected_mode = self._get_expected_mode_from_schedule(schedule)

        # If current mode doesn't match expected, we're in override
        return current_mode != expected_mode

    def _get_expected_mode_from_schedule(self, schedule: list[dict]) -> str:
        """Determine what mode should be active based on schedule."""
        if not schedule:
            return "MATCH"  # Default to MATCH when no schedule

        now = dt_util.now()
        current_time = now.time()

        # Find which schedule slot we're currently in
        for slot in schedule:
            begin = slot.get("begin", {})
            end = slot.get("end", {})

            begin_time = time(
                hour=begin.get("hour", 0),
                minute=begin.get("minute", 0),
                second=begin.get("second", 0),
            )
            end_time = time(
                hour=end.get("hour", 0),
                minute=end.get("minute", 0),
                second=end.get("second", 0),
            )

            # Handle slots that cross midnight
            if begin_time <= end_time:
                # Normal case: slot within same day
                if begin_time <= current_time < end_time:
                    return slot.get("mode", "MATCH")
            else:
                # Slot crosses midnight
                if current_time >= begin_time or current_time < end_time:
                    return slot.get("mode", "MATCH")

        # If we're not in any scheduled slot, default to MATCH
        return "MATCH"


class AnodeBatteryOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for battery online status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._battery_id = battery_id
        self._hub_id = hub_id
        self._attr_unique_id = f"{battery_id}_online"
        self._attr_name = f"Anode Battery {battery_id} Online"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return true if battery is online."""
        # Battery is online if it appears in the status response
        for battery in self.coordinator.data.get("battery", []):
            if battery["id"] == self._battery_id:
                return True
        return False


class AnodeMeterOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for meter online status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        hub_id: str,
        meter_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._hub_id = hub_id
        self._attr_unique_id = f"{meter_id}_online"
        self._attr_name = f"Anode Meter {meter_id} Online"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return true if meter is online."""
        # Meter is online if it appears in the status response
        for meter in self.coordinator.data.get("meter", []):
            if meter["id"] == self._meter_id:
                return True
        return False

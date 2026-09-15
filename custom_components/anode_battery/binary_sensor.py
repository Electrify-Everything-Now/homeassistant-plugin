"""Binary sensor platform for the Anode integration."""
from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AnodeConfigEntry, AnodeModeCoordinator, AnodeStatusCoordinator
from .entity import AnodeEntity, async_setup_dynamic_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode binary sensors."""
    runtime = entry.runtime_data

    def build() -> Iterator[Entity]:
        status = runtime.status.data
        yield AnodeHubOnlineSensor(runtime.status)
        yield AnodeOverrideActiveSensor(runtime.mode)
        for device_id in (*status.batteries, *status.meters):
            yield AnodeSubDeviceOnlineSensor(runtime.status, device_id)

    async_setup_dynamic_entities(entry, async_add_entities, build)


class AnodeHubOnlineSensor(AnodeEntity[AnodeStatusCoordinator], BinarySensorEntity):
    """Whether the hub answers through the Anode cloud."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: AnodeStatusCoordinator) -> None:
        super().__init__(coordinator, coordinator.hub_id, "online")

    @property
    def available(self) -> bool:
        """Stay available so an unreachable hub reads as disconnected."""
        return True

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.coordinator.data.online
        )


class AnodeSubDeviceOnlineSensor(AnodeEntity[AnodeStatusCoordinator], BinarySensorEntity):
    """Whether the hub currently reports a battery or meter as connected."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: AnodeStatusCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "online")

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        device = data.batteries.get(self._device_id) or data.meters.get(self._device_id)
        # Older firmware has no online flag; being listed means connected.
        return device is not None and device.online is not False


class AnodeOverrideActiveSensor(AnodeEntity[AnodeModeCoordinator], BinarySensorEntity):
    """Whether the hub is running a different mode from its schedule."""

    _attr_translation_key = "override_active"

    def __init__(self, coordinator: AnodeModeCoordinator) -> None:
        super().__init__(coordinator, coordinator.hub_id, "override_active")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.override_active is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.override_active

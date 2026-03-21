"""Button platform for Anode integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, CONF_HUB_ID, OperatingMode
from .coordinator import AnodeModeCoordinator, AnodeAPIClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode button entities."""
    hub_id = entry.data[CONF_HUB_ID]
    coordinators = hass.data[DOMAIN][entry.entry_id]

    mode_coordinator: AnodeModeCoordinator = coordinators["mode_coordinator"]
    api_client: AnodeAPIClient = coordinators["api_client"]

    entities: list[ButtonEntity] = [
        AnodeCancelOverrideButton(mode_coordinator, api_client, hub_id, entry.entry_id)
    ]

    async_add_entities(entities)


class AnodeCancelOverrideButton(CoordinatorEntity, ButtonEntity):
    """Button to cancel any active mode override."""

    def __init__(
        self,
        coordinator: AnodeModeCoordinator,
        api_client: AnodeAPIClient,
        hub_id: str,
        entry_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._api_client = api_client
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_cancel_override"
        self._attr_name = f"Anode Hub {hub_id} Cancel Override"
        self._attr_icon = "mdi:cancel"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            # Set override with timeout=0 to cancel
            # Use IDLE mode with 0 timeout as a cancel operation
            await self._api_client.set_override(OperatingMode.IDLE.value, 0)
            _LOGGER.info("Cancelled mode override for hub %s", self._hub_id)

            # Request coordinator refresh to update mode
            await self.coordinator.async_request_refresh_soon()

        except Exception as err:
            _LOGGER.error("Failed to cancel override: %s", err)
            raise

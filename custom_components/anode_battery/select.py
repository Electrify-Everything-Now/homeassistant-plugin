"""Select platform for Anode Battery integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    CONF_HUB_ID,
    OperatingMode,
    OVERRIDE_TIME_OPTIONS,
    OVERRIDE_TIME_LABELS,
)
from .coordinator import AnodeModeCoordinator, AnodeAPIClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode Battery select entities."""
    hub_id = entry.data[CONF_HUB_ID]
    coordinators = hass.data[DOMAIN][entry.entry_id]

    mode_coordinator: AnodeModeCoordinator = coordinators["mode_coordinator"]
    api_client: AnodeAPIClient = coordinators["api_client"]

    entities: list[SelectEntity] = []

    # Create select entities for each mode override
    for mode in OperatingMode:
        entities.append(
            AnodeModeOverrideSelect(
                mode_coordinator,
                api_client,
                hub_id,
                mode,
                entry.entry_id,
            )
        )

    async_add_entities(entities)


class AnodeModeOverrideSelect(CoordinatorEntity, SelectEntity):
    """Select entity for mode override with duration."""

    def __init__(
        self,
        coordinator: AnodeModeCoordinator,
        api_client: AnodeAPIClient,
        hub_id: str,
        mode: OperatingMode,
        entry_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._api_client = api_client
        self._hub_id = hub_id
        self._mode = mode
        self._entry_id = entry_id

        mode_lower = mode.value.lower()
        self._attr_unique_id = f"{hub_id}_{mode_lower}_override"
        self._attr_name = f"Anode Hub {hub_id} {mode.value.title()} Override"
        self._attr_icon = self._get_icon_for_mode(mode)
        self._attr_options = list(OVERRIDE_TIME_LABELS.values())
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    def _get_icon_for_mode(self, mode: OperatingMode) -> str:
        """Get icon for the mode."""
        icons = {
            OperatingMode.CHARGE: "mdi:battery-charging",
            OperatingMode.DISCHARGE: "mdi:battery-minus",
            OperatingMode.IDLE: "mdi:battery-off",
            OperatingMode.MATCH: "mdi:battery-sync",
        }
        return icons.get(mode, "mdi:battery")

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        # We don't track the active state, so return None (no selection)
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Find the corresponding time value
        timeout_seconds = None
        for key, label in OVERRIDE_TIME_LABELS.items():
            if label == option:
                timeout_seconds = OVERRIDE_TIME_OPTIONS[key]
                break

        if timeout_seconds is None:
            _LOGGER.error("Invalid option selected: %s", option)
            return

        # Set the override
        try:
            await self._api_client.set_override(self._mode.value, timeout_seconds)
            _LOGGER.info(
                "Set override: mode=%s, timeout=%d seconds",
                self._mode.value,
                timeout_seconds,
            )

            # Request coordinator refresh
            await self.coordinator.async_request_refresh_soon()

        except Exception as err:
            _LOGGER.error("Failed to set override: %s", err)
            raise

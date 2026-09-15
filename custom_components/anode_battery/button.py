"""Button platform for the Anode integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AnodeConfigEntry, AnodeModeCoordinator, AnodeRuntimeData
from .entity import AnodeEntity, async_run_command

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode buttons."""
    async_add_entities([AnodeCancelOverrideButton(entry.runtime_data)])


class AnodeCancelOverrideButton(AnodeEntity[AnodeModeCoordinator], ButtonEntity):
    """End any override so the hub follows its schedule again."""

    _attr_translation_key = "cancel_override"

    def __init__(self, runtime: AnodeRuntimeData) -> None:
        super().__init__(runtime.mode, runtime.hub_id, "cancel_override")
        self._runtime = runtime

    async def async_press(self) -> None:
        runtime = self._runtime
        await async_run_command(
            self.hass,
            self.coordinator.config_entry,
            runtime.client.cancel_override(runtime.hub_id),
        )
        self.coordinator.async_note_override(None)

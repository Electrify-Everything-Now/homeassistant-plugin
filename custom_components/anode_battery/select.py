"""Select platform for the Anode integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OperatingMode
from .coordinator import AnodeConfigEntry, AnodeModeCoordinator, AnodeRuntimeData
from .entity import AnodeEntity, async_run_command, is_read_only

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode select entities."""
    if is_read_only(entry):
        return
    async_add_entities([AnodeOverrideModeSelect(entry.runtime_data)])


class AnodeOverrideModeSelect(AnodeEntity[AnodeModeCoordinator], SelectEntity):
    """Shows the running mode; choosing a mode overrides the schedule.

    The override lasts for the hub's Override duration setting.
    """

    _attr_translation_key = "override_mode"
    _attr_options = [mode.value.lower() for mode in OperatingMode]

    def __init__(self, runtime: AnodeRuntimeData) -> None:
        super().__init__(runtime.mode, runtime.hub_id, "override_mode")
        self._runtime = runtime

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.data.mode
        return mode.value.lower() if mode else None

    async def async_select_option(self, option: str) -> None:
        runtime = self._runtime
        mode = OperatingMode(option.upper())
        await async_run_command(
            self.hass,
            self.coordinator.config_entry,
            runtime.client.set_override(
                runtime.hub_id, mode, runtime.override_duration_min * 60
            ),
        )
        self.coordinator.async_note_override(mode)

"""Base entity and shared helpers for the Anode integration."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import AnodeAuthError, AnodeError, AnodeHubOfflineError
from .const import DOMAIN
from .coordinator import AnodeConfigEntry


def device_info(device_id: str) -> DeviceInfo:
    """Link an entity to a device created by ``async_sync_devices``."""
    return DeviceInfo(identifiers={(DOMAIN, device_id)})


class AnodeEntity[CoordinatorT: DataUpdateCoordinator](CoordinatorEntity[CoordinatorT]):
    """An entity belonging to one hub, battery or meter.

    Unique ids are ``<device id>_<key>``, matching every earlier release so
    entity ids and statistics carry over.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: CoordinatorT, device_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = device_info(device_id)


@callback
def async_setup_dynamic_entities(
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[], Iterable[Entity]],
    *extra_coordinators: DataUpdateCoordinator,
) -> None:
    """Add entities now, and again whenever new ones become possible.

    ``build`` yields every entity that should exist for the current data;
    entities already added are skipped. It re-runs when hub status changes
    (a battery or meter is paired) and when any extra coordinator updates.
    """
    added: set[str] = set()

    @callback
    def add_new() -> None:
        if entry.runtime_data.status.data is None:
            return
        new = [entity for entity in build() if entity.unique_id not in added]
        if new:
            added.update(entity.unique_id for entity in new if entity.unique_id)
            async_add_entities(new)

    add_new()
    for coordinator in (entry.runtime_data.status, *extra_coordinators):
        entry.async_on_unload(coordinator.async_add_listener(add_new))


async def async_run_command(
    hass: HomeAssistant, entry: AnodeConfigEntry, command: Awaitable[None]
) -> None:
    """Await a hub command, turning client errors into user-facing errors."""
    try:
        await command
    except AnodeAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            "Anode rejected the credentials",
            translation_domain=DOMAIN,
            translation_key="auth_failed",
        ) from err
    except AnodeHubOfflineError as err:
        raise HomeAssistantError(
            "The hub did not respond",
            translation_domain=DOMAIN,
            translation_key="hub_offline_command",
        ) from err
    except AnodeError as err:
        raise HomeAssistantError(
            f"The hub did not accept the command: {err}",
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": str(err)},
        ) from err

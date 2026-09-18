"""Update platform for the Anode integration.

Home Assistant reports firmware here but never applies it. Flashing a device is
`device:service` work that runs over MQTT and takes the hub through a reboot,
and asking every household to grant that scope so a dashboard can show a badge
is the wrong trade. So these entities are deliberately install-less: they say
what a device is running, what it could be running, and where to go to do it.

Nothing here compares version strings. The hub knows what its devices run and
the server knows what the account's firmware train holds; the server joins the
two and answers `updateAvailable`, so this platform reads a verdict rather than
reaching one. ``version_is_newer`` is overridden for exactly that reason.
"""
from __future__ import annotations

from collections.abc import Iterator
import logging
from typing import Protocol

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import AnodeError, HubStatus, ProductCode, ReleaseNote
from .coordinator import AnodeConfigEntry, AnodeStatusCoordinator
from .entity import AnodeEntity, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

#: Where a person actually applies one of these. Home Assistant cannot, so
#: every set of release notes ends with this line.
UPDATE_INSTRUCTIONS = (
    "Home Assistant cannot install Anode firmware. Apply this update from the "
    "Anode app, or from your hub page at "
    "https://anode.energy/dashboard/user/overview."
)


class FirmwareState(Protocol):
    """The firmware fields a hub and its sub-devices report alike."""

    version: str | None
    latest_version: str | None
    update_available: bool
    ota_in_progress: bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode update entities."""
    runtime = entry.runtime_data

    def build() -> Iterator[Entity]:
        status = runtime.status.data
        # An entity only exists where the server reached an answer. A device on
        # a product the train has no images for would otherwise sit permanently
        # at "unknown", which reads as a fault rather than as "not applicable".
        # Entities are rebuilt on every status update, so one appears by itself
        # if the train later gains an image for it.
        if status.latest_version is not None:
            yield AnodeUpdateEntity(runtime.status, status.hub_id, ProductCode.HUB)
        for device_id, battery in status.batteries.items():
            if battery.latest_version is not None:
                yield AnodeUpdateEntity(runtime.status, device_id, ProductCode.BATTERY)
        for device_id, meter in status.meters.items():
            if meter.latest_version is not None:
                yield AnodeUpdateEntity(runtime.status, device_id, ProductCode.METER)

    async_setup_dynamic_entities(entry, async_add_entities, build)


def _firmware_state(status: HubStatus, device_id: str) -> FirmwareState | None:
    """The firmware fields for one device, hub or sub-device."""
    if device_id == status.hub_id:
        return status
    return status.batteries.get(device_id) or status.meters.get(device_id)


def _release_notes_body(note: ReleaseNote | None) -> str:
    """Render a note as markdown, always ending with how to apply it."""
    parts = []
    if note is not None:
        parts.append(note.summary)
        if note.whats_new:
            parts.append(f"## What's new\n\n{note.whats_new}")
        if note.whats_fixed:
            parts.append(f"## What's fixed\n\n{note.whats_fixed}")
    parts.append(UPDATE_INSTRUCTIONS)
    return "\n\n".join(parts)


class AnodeUpdateEntity(AnodeEntity[AnodeStatusCoordinator], UpdateEntity):
    """Firmware available for one hub, battery or meter."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_translation_key = "firmware"
    # No INSTALL: see the module docstring. PROGRESS is supported without it so
    # that a flash started from the app or the web dashboard shows here as
    # installing rather than as a badge that will not go away.
    _attr_supported_features = (
        UpdateEntityFeature.RELEASE_NOTES | UpdateEntityFeature.PROGRESS
    )

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        device_id: str,
        product_code: ProductCode,
    ) -> None:
        super().__init__(coordinator, device_id, "firmware")
        self._product_code = product_code
        # The note for one version, kept so reopening the dialog does not
        # re-fetch. Failures are not cached, only answers.
        self._note: tuple[str, ReleaseNote | None] | None = None

    @property
    def _state(self) -> FirmwareState | None:
        data = self.coordinator.data
        return _firmware_state(data, self._device_id) if data is not None else None

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    @property
    def installed_version(self) -> str | None:
        state = self._state
        return state.version if state else None

    @property
    def latest_version(self) -> str | None:
        state = self._state
        return state.latest_version if state else None

    @property
    def in_progress(self) -> bool:
        state = self._state
        return bool(state and state.ota_in_progress)

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Report the server's verdict instead of comparing version strings.

        Home Assistant would otherwise parse both with AwesomeVersion, which
        does not know this scheme: a dev build carries a commit count and hash
        (``v1.2.3-45-gabcdef1``) that decide ordering. The server has already
        made this comparison against the account's own train, and a device
        running something newer than its train is not offered a downgrade.
        """
        state = self._state
        return bool(state and state.update_available)

    async def async_release_notes(self) -> str | None:
        """Return what changed, and how to apply it."""
        state = self._state
        if state is None or (version := state.latest_version) is None:
            return None

        if self._note is not None and self._note[0] == version:
            return _release_notes_body(self._note[1])

        try:
            note = await self.coordinator.client.get_release_note(
                version, self._product_code
            )
        except AnodeError as err:
            # Notes are a nicety, so a failed read still tells the person where
            # to go. Not cached: it is worth another try next time.
            _LOGGER.debug("Could not read release notes for %s: %s", version, err)
            return _release_notes_body(None)

        self._note = (version, note)
        return _release_notes_body(note)

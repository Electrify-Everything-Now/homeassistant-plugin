"""Update platform for the Anode integration.

Every hub, battery and meter gets an entity saying what it runs and what it
could be running. Where the key may install firmware, the entity can also
install it, and is then listed under Settings → Updates: Home Assistant only
lists update entities that can install.

Installing asks the server to move one device up to the latest image on the
owner's own train. The request names no image; the server picks it the same
way it decides ``updateAvailable``, so what is offered is what is installed, and
a key can never move a device down or onto another train.

Nothing here compares version strings. The hub knows what its devices run and
the server knows what the account's firmware train holds; the server joins the
two and answers `updateAvailable`, so this platform reads a verdict rather than
reaching one. ``version_is_newer`` is overridden for exactly that reason.

The hub updates one device at a time and refuses a second command while one
runs, so installs are sent one at a time per hub and refused here, with the
device that is updating named, rather than sent to be refused there.
"""
from __future__ import annotations

from collections.abc import Iterator
import logging
from typing import Any, Protocol

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    AnodeError,
    AnodeHubRefusedError,
    AnodeUpdateInProgressError,
    FirmwareUpdateResult,
    HubStatus,
    ProductCode,
    ReleaseNote,
)
from .const import CONF_FIRMWARE, DOMAIN
from .coordinator import (
    AnodeConfigEntry,
    AnodeFirmwareCoordinator,
    AnodeStatusCoordinator,
)
from .entity import AnodeEntity, async_run_command, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)

# Installs are serialised per hub by the firmware coordinator's lock instead,
# which also lets a second one be refused rather than queued.
PARALLEL_UPDATES = 0

#: How to apply an update where this key may not install one. Ends every set of
#: release notes such an entity shows.
UPDATE_INSTRUCTIONS = (
    "Home Assistant can install Anode firmware once it is linked with "
    "permission to: reconfigure the integration and link again. Until then, "
    "apply this update from the Anode app, or from your hub page at "
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
    can_install = bool(entry.data.get(CONF_FIRMWARE))

    def entity(device_id: str, product_code: ProductCode) -> AnodeUpdateEntity:
        return AnodeUpdateEntity(
            runtime.status, runtime.firmware, device_id, product_code, can_install
        )

    def build() -> Iterator[Entity]:
        status = runtime.status.data
        # An entity only exists where the server reached an answer. A device on
        # a product the train has no images for would otherwise sit permanently
        # at "unknown", which reads as a fault rather than as "not applicable".
        # Entities are rebuilt on every status update, so one appears by itself
        # if the train later gains an image for it.
        if status.latest_version is not None:
            yield entity(status.hub_id, ProductCode.HUB)
        for device_id, battery in status.batteries.items():
            if battery.latest_version is not None:
                yield entity(device_id, ProductCode.BATTERY)
        for device_id, meter in status.meters.items():
            if meter.latest_version is not None:
                yield entity(device_id, ProductCode.METER)

    async_setup_dynamic_entities(entry, async_add_entities, build)


def _firmware_state(status: HubStatus, device_id: str) -> FirmwareState | None:
    """The firmware fields for one device, hub or sub-device."""
    if device_id == status.hub_id:
        return status
    return status.batteries.get(device_id) or status.meters.get(device_id)


def _device_online(status: HubStatus, device_id: str) -> bool:
    """Whether a device can be reached; unknown counts as reachable."""
    if device_id == status.hub_id:
        return status.online
    device = status.batteries.get(device_id) or status.meters.get(device_id)
    return device is None or device.online is not False


def _release_notes_body(note: ReleaseNote | None, footer: str | None) -> str | None:
    """Render a note as markdown, ending with how to apply it where needed."""
    parts = []
    if note is not None:
        parts.append(note.summary)
        if note.whats_new:
            parts.append(f"## What's new\n\n{note.whats_new}")
        if note.whats_fixed:
            parts.append(f"## What's fixed\n\n{note.whats_fixed}")
    if footer:
        parts.append(footer)
    return "\n\n".join(parts) or None


class AnodeUpdateEntity(AnodeEntity[AnodeStatusCoordinator], UpdateEntity):
    """Firmware available for one hub, battery or meter."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_translation_key = "firmware"
    # PROGRESS even without INSTALL, so that an update started from the app or
    # the web dashboard shows here as installing rather than as a badge that
    # will not go away.
    _attr_supported_features = (
        UpdateEntityFeature.RELEASE_NOTES | UpdateEntityFeature.PROGRESS
    )

    def __init__(
        self,
        coordinator: AnodeStatusCoordinator,
        firmware: AnodeFirmwareCoordinator,
        device_id: str,
        product_code: ProductCode,
        can_install: bool,
    ) -> None:
        super().__init__(coordinator, device_id, "firmware")
        self._firmware = firmware
        self._product_code = product_code
        self._can_install = can_install
        if can_install:
            self._attr_supported_features |= UpdateEntityFeature.INSTALL
        # While the install request is out. Home Assistant does not mark an
        # entity that reports its own progress as installing, so without this a
        # second press would get past its check while the first is on its way.
        self._sending = False
        # The note for one version, kept so reopening the dialog does not
        # re-fetch. Failures are not cached, only answers.
        self._note: tuple[str, ReleaseNote | None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._firmware.async_add_listener(self._handle_coordinator_update)
        )

    @property
    def _state(self) -> FirmwareState | None:
        data = self.coordinator.data
        return _firmware_state(data, self._device_id) if data is not None else None

    @property
    def available(self) -> bool:
        # A device reboots to finish an update, and a hub doing so stops
        # answering status for a minute. That is the update working, so the
        # last status stands in until it answers again.
        return self._state is not None and (
            super().available or self._firmware.is_updating(self._device_id)
        )

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
        return self._sending or self._firmware.is_updating(self._device_id)

    @property
    def update_percentage(self) -> int | None:
        return self._firmware.progress(self._device_id)

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

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Ask the server to move this device up to the latest firmware."""
        firmware = self._firmware
        # Home Assistant only checks this entity. The hub takes one update at a
        # time, so one running on any of its devices refuses this one too.
        if firmware.lock.locked():
            raise self._in_progress_error(None)
        if updating := firmware.updating():
            raise self._in_progress_error(updating[0])
        if self.coordinator.data is not None and not _device_online(
            self.coordinator.data, self._device_id
        ):
            raise HomeAssistantError(
                "The device is offline",
                translation_domain=DOMAIN,
                translation_key="update_device_offline",
                translation_placeholders={"device": self._device_name(self._device_id)},
            )

        async with firmware.lock:
            self._sending = True
            self.async_write_ha_state()
            try:
                result = await async_run_command(
                    self.hass, self.coordinator.config_entry, self._async_send()
                )
            finally:
                self._sending = False

        if not any(update.device_id == self._device_id for update in result.updates):
            # Status was out of date: the device is current after all.
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                "Nothing to update",
                translation_domain=DOMAIN,
                translation_key="update_not_needed",
                translation_placeholders={"device": self._device_name(self._device_id)},
            )
        firmware.async_note_started(self._device_id)

    async def _async_send(self) -> FirmwareUpdateResult:
        """Send the update, naming the device when another is updating."""
        try:
            return await self.coordinator.client.update_firmware(
                self.coordinator.hub_id, self._device_id
            )
        except AnodeUpdateInProgressError as err:
            # Something this integration did not start, so status has not seen
            # it: read it now so the entities catch up.
            self.hass.async_create_task(self.coordinator.async_request_refresh())
            raise self._in_progress_error(err.device_id) from err
        except AnodeHubRefusedError as err:
            raise HomeAssistantError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="update_refused",
                translation_placeholders={"info": err.info or str(err)},
            ) from err

    def _in_progress_error(self, device_id: str | None) -> HomeAssistantError:
        if device_id is None:
            return HomeAssistantError(
                "A firmware update is already running on the hub",
                translation_domain=DOMAIN,
                translation_key="update_in_progress_hub",
            )
        return HomeAssistantError(
            f"A firmware update is already running on {device_id}",
            translation_domain=DOMAIN,
            translation_key="update_in_progress",
            translation_placeholders={"device": self._device_name(device_id)},
        )

    def _device_name(self, device_id: str) -> str:
        """What the person calls a device, falling back to its id."""
        device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, device_id)})
        if device is None:
            return device_id
        return device.name_by_user or device.name or device_id

    async def async_release_notes(self) -> str | None:
        """Return what changed, and how to apply it where this cannot."""
        footer = None if self._can_install else UPDATE_INSTRUCTIONS
        state = self._state
        if state is None or (version := state.latest_version) is None:
            return None

        if self._note is not None and self._note[0] == version:
            return _release_notes_body(self._note[1], footer)

        try:
            note = await self.coordinator.client.get_release_note(
                version, self._product_code
            )
        except AnodeError as err:
            # Notes are a nicety, so a failed read still tells the person where
            # to go. Not cached: it is worth another try next time.
            _LOGGER.debug("Could not read release notes for %s: %s", version, err)
            return _release_notes_body(None, footer)

        self._note = (version, note)
        return _release_notes_body(note, footer)

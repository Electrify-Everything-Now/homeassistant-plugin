"""Data update coordinators for the Anode integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from typing import NoReturn

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AnodeAuthError,
    AnodeClient,
    AnodeError,
    AnodeHubOfflineError,
    BatteryReading,
    DeviceMetadata,
    HubStatus,
    MeterReading,
    OperatingMode,
    PowerLimit,
    PowerLimitKey,
    ScheduleSlot,
    SocLimits,
)
from .const import (
    DEFAULT_OVERRIDE_DURATION_MIN,
    DOMAIN,
    MODE_POLL_INTERVAL,
    MODE_SETTLE_DELAY,
    SETTINGS_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type AnodeConfigEntry = ConfigEntry[AnodeRuntimeData]


@dataclass
class AnodeRuntimeData:
    """Everything a loaded config entry holds."""

    client: AnodeClient
    hub_id: str
    status: AnodeStatusCoordinator
    telemetry: AnodeTelemetryCoordinator
    mode: AnodeModeCoordinator
    settings: AnodeSettingsCoordinator
    # Set by the Override duration number, used by the Override mode select.
    override_duration_min: int = DEFAULT_OVERRIDE_DURATION_MIN


class _AnodeCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Shared plumbing for Anode coordinators."""

    config_entry: AnodeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AnodeConfigEntry,
        client: AnodeClient,
        hub_id: str,
        *,
        name: str,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {hub_id} {name}",
            update_interval=update_interval,
        )
        self.client = client
        self.hub_id = hub_id

    def _raise_update_error(self, what: str, err: BaseException) -> NoReturn:
        """Raise what the coordinator should raise for a failed read."""
        if isinstance(err, AnodeAuthError):
            raise ConfigEntryAuthFailed(
                f"Anode rejected the credentials while reading {what}",
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        if isinstance(err, AnodeHubOfflineError):
            raise UpdateFailed(
                f"Hub {self.hub_id} did not respond",
                translation_domain=DOMAIN,
                translation_key="hub_offline",
                translation_placeholders={"hub_id": self.hub_id},
            ) from err
        if isinstance(err, AnodeError):
            raise UpdateFailed(
                f"Could not read {what}: {err}",
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"what": what, "error": str(err)},
            ) from err
        raise err


class AnodeStatusCoordinator(_AnodeCoordinator[HubStatus]):
    """Hub status: which batteries and meters exist, firmware, aliases."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AnodeConfigEntry,
        client: AnodeClient,
        hub_id: str,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass, entry, client, hub_id, name="status", update_interval=update_interval
        )
        self._metadata: dict[str, DeviceMetadata] = {}

    async def _async_update_data(self) -> HubStatus:
        status, metadata = await asyncio.gather(
            self.client.get_hub_status(self.hub_id),
            self.client.get_device_metadata(self.hub_id),
            return_exceptions=True,
        )
        if isinstance(status, BaseException):
            self._raise_update_error("hub status", status)
        if isinstance(metadata, AnodeError):
            # Installer accounts and older backends cannot read metadata.
            # Keep the last aliases rather than dropping them.
            _LOGGER.debug("Device metadata unavailable for %s: %s", self.hub_id, metadata)
        elif isinstance(metadata, BaseException):
            raise metadata
        else:
            self._metadata = metadata
        return status.with_metadata(self._metadata)


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Latest battery and meter readings.

    Readings are kept per device after it stops reporting, so energy
    calculations never substitute zero for a device that dropped out. Use
    ``is_current`` to decide whether a device's reading is live.
    """

    batteries: dict[str, BatteryReading]
    meters: dict[str, MeterReading]
    reported: frozenset[str]
    batteries_current: bool
    meters_current: bool

    def is_current(self, device_id: str) -> bool:
        """Whether the device reported in the latest successful read."""
        return device_id in self.reported


class AnodeTelemetryCoordinator(_AnodeCoordinator[Telemetry]):
    """Power, state of charge and energy counters for every device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AnodeConfigEntry,
        client: AnodeClient,
        hub_id: str,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass, entry, client, hub_id, name="telemetry", update_interval=update_interval
        )
        self._failing: set[str] = set()

    async def _async_update_data(self) -> Telemetry:
        batteries, meters = await asyncio.gather(
            self.client.get_batteries(self.hub_id),
            self.client.get_meters(self.hub_id),
            return_exceptions=True,
        )
        for result in (batteries, meters):
            if isinstance(result, AnodeAuthError) or (
                isinstance(result, BaseException) and not isinstance(result, AnodeError)
            ):
                self._raise_update_error("telemetry", result)
        if isinstance(batteries, AnodeError) and isinstance(meters, AnodeError):
            self._raise_update_error("telemetry", batteries)

        previous = self.data
        reported: set[str] = set()

        battery_map = dict(previous.batteries) if previous else {}
        if isinstance(batteries, AnodeError):
            self._note_failure("battery", batteries)
        else:
            self._note_success("battery")
            battery_map.update(batteries)
            reported.update(batteries)

        meter_map = dict(previous.meters) if previous else {}
        if isinstance(meters, AnodeError):
            self._note_failure("meter", meters)
        else:
            self._note_success("meter")
            meter_map.update(meters)
            reported.update(meters)

        return Telemetry(
            batteries=battery_map,
            meters=meter_map,
            reported=frozenset(reported),
            batteries_current=not isinstance(batteries, AnodeError),
            meters_current=not isinstance(meters, AnodeError),
        )

    def _note_failure(self, kind: str, err: AnodeError) -> None:
        if kind not in self._failing:
            self._failing.add(kind)
            _LOGGER.warning("Could not read %s readings from hub %s: %s", kind, self.hub_id, err)

    def _note_success(self, kind: str) -> None:
        if kind in self._failing:
            self._failing.discard(kind)
            _LOGGER.info("Reading %s data from hub %s again", kind, self.hub_id)


def scheduled_mode_at(
    schedule: tuple[ScheduleSlot, ...] | list[ScheduleSlot], moment: datetime
) -> tuple[OperatingMode, ScheduleSlot | None]:
    """Return the mode the schedule asks for at a moment, and the slot."""
    local = moment.astimezone(dt_util.get_default_time_zone()).time()
    for slot in schedule:
        if slot.contains(local):
            return slot.mode, slot
    return OperatingMode.MATCH, None


def next_schedule_change(
    schedule: tuple[ScheduleSlot, ...] | list[ScheduleSlot], now: datetime
) -> tuple[datetime, OperatingMode] | None:
    """Return when the scheduled mode next changes, and the mode it changes to.

    Schedule times are in the hub's local time, which is assumed to match
    Home Assistant's time zone.
    """
    if not schedule:
        return None
    tz = dt_util.get_default_time_zone()
    local_now = now.astimezone(tz)
    current, _ = scheduled_mode_at(schedule, local_now)
    boundaries = sorted({t for slot in schedule for t in (slot.begin, slot.end)})
    candidates = sorted(
        moment
        for day in (local_now.date(), local_now.date() + timedelta(days=1))
        for t in boundaries
        if (moment := datetime.combine(day, t, tzinfo=tz)) > local_now
    )
    for moment in candidates:
        mode, _ = scheduled_mode_at(schedule, moment)
        if mode is not current:
            return moment, mode
    return None


@dataclass(frozen=True, slots=True)
class ModeState:
    """What the hub is doing and what its schedule asks for."""

    mode: OperatingMode | None
    schedule: tuple[ScheduleSlot, ...]
    scheduled_mode: OperatingMode
    active_slot: ScheduleSlot | None
    next_mode: OperatingMode | None
    next_change: datetime | None

    @property
    def override_active(self) -> bool | None:
        """Whether the hub is running a different mode from its schedule.

        The API does not report overrides directly, so this compares the
        running mode with the schedule. Octopus smart-charging dispatches also
        show as an override.
        """
        if self.mode is None:
            return None
        if self.mode is self.scheduled_mode:
            return False
        # A slot with a target state of charge settles into IDLE once reached.
        if (
            self.mode is OperatingMode.IDLE
            and self.active_slot is not None
            and self.active_slot.target_soc
        ):
            return False
        return True


class AnodeModeCoordinator(_AnodeCoordinator[ModeState]):
    """Running mode and schedule.

    Polls slowly, and additionally refreshes just after each scheduled mode
    change so mode entities flip at the right time.
    """

    def __init__(
        self, hass: HomeAssistant, entry: AnodeConfigEntry, client: AnodeClient, hub_id: str
    ) -> None:
        super().__init__(
            hass, entry, client, hub_id, name="mode", update_interval=MODE_POLL_INTERVAL
        )
        self._unsub_transition: CALLBACK_TYPE | None = None
        self._unsub_confirm: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> ModeState:
        mode, schedule = await asyncio.gather(
            self.client.get_mode(self.hub_id),
            self.client.get_schedule(self.hub_id),
            return_exceptions=True,
        )
        if isinstance(mode, BaseException):
            self._raise_update_error("mode", mode)
        if isinstance(schedule, BaseException):
            self._raise_update_error("schedule", schedule)

        now = dt_util.utcnow()
        scheduled, slot = scheduled_mode_at(schedule, now)
        change = next_schedule_change(schedule, now)
        self._track_transition(change[0] if change else None)
        return ModeState(
            mode=mode,
            schedule=tuple(schedule),
            scheduled_mode=scheduled,
            active_slot=slot,
            next_mode=change[1] if change else None,
            next_change=change[0] if change else None,
        )

    @callback
    def _track_transition(self, when: datetime | None) -> None:
        if self._unsub_transition:
            self._unsub_transition()
            self._unsub_transition = None
        if when is not None:
            self._unsub_transition = async_track_point_in_utc_time(
                self.hass, self._async_handle_transition, when + MODE_SETTLE_DELAY
            )

    async def _async_handle_transition(self, _now: datetime) -> None:
        self._unsub_transition = None
        await self.async_refresh()

    @callback
    def async_note_override(self, mode: OperatingMode | None) -> None:
        """Show an override sent from Home Assistant now, then confirm it.

        ``None`` means the override was cancelled.
        """
        if self.data is not None:
            running = self.data.scheduled_mode if mode is None else mode
            self.async_set_updated_data(replace(self.data, mode=running))
        if self._unsub_confirm:
            self._unsub_confirm()
        self._unsub_confirm = async_call_later(
            self.hass, MODE_SETTLE_DELAY, self._async_confirm_override
        )

    async def _async_confirm_override(self, _now: datetime) -> None:
        self._unsub_confirm = None
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        """Cancel scheduled refreshes."""
        for unsub in (self._unsub_transition, self._unsub_confirm):
            if unsub:
                unsub()
        self._unsub_transition = self._unsub_confirm = None
        await super().async_shutdown()


@dataclass(frozen=True, slots=True)
class HubSettings:
    """Configurable hub limits."""

    soc_limits: dict[str, SocLimits]
    power_limits: dict[PowerLimitKey, PowerLimit]


class AnodeSettingsCoordinator(_AnodeCoordinator[HubSettings]):
    """State-of-charge windows and power limits."""

    def __init__(
        self, hass: HomeAssistant, entry: AnodeConfigEntry, client: AnodeClient, hub_id: str
    ) -> None:
        super().__init__(
            hass,
            entry,
            client,
            hub_id,
            name="settings",
            update_interval=SETTINGS_POLL_INTERVAL,
        )

    async def _async_update_data(self) -> HubSettings:
        soc, charge, discharge = await asyncio.gather(
            self.client.get_soc_limits(self.hub_id),
            self.client.get_power_limit(self.hub_id, PowerLimitKey.MAX_CHARGE),
            self.client.get_power_limit(self.hub_id, PowerLimitKey.MAX_DISCHARGE),
            return_exceptions=True,
        )
        results = (soc, charge, discharge)
        for result in results:
            if isinstance(result, AnodeAuthError) or (
                isinstance(result, BaseException) and not isinstance(result, AnodeError)
            ):
                self._raise_update_error("settings", result)
        if all(isinstance(result, AnodeError) for result in results):
            self._raise_update_error("settings", soc)

        previous = self.data
        if isinstance(soc, AnodeError):
            _LOGGER.debug("Could not read SOC limits for %s: %s", self.hub_id, soc)
            soc_limits = previous.soc_limits if previous else {}
        else:
            soc_limits = soc

        power_limits = dict(previous.power_limits) if previous else {}
        for key, result in (
            (PowerLimitKey.MAX_CHARGE, charge),
            (PowerLimitKey.MAX_DISCHARGE, discharge),
        ):
            if isinstance(result, AnodeError):
                _LOGGER.debug("Could not read %s for %s: %s", key, self.hub_id, result)
            else:
                power_limits[key] = result

        return HubSettings(soc_limits=soc_limits, power_limits=power_limits)

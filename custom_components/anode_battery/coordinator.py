"""Data update coordinators for Anode integration."""
from __future__ import annotations

from datetime import timedelta, datetime, time
import logging
from typing import Any
import base64

import asyncio

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import CONF_EMAIL
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    API_BASE_URL,
    API_TIMEOUT,
    CONF_API_KEY,
    CONF_HUB_ID,
    CONF_STATUS_INTERVAL,
    CONF_DEVICE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_DEVICE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class AnodeAPIClient:
    """API client for Anode."""

    def __init__(self, hass: HomeAssistant, email: str, api_key: str, hub_id: str) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.email = email
        self.api_key = api_key
        self.hub_id = hub_id
        self.session = async_get_clientsession(hass)

        # Create Basic Auth header
        credentials = f"{email}:{api_key}"
        b64_credentials = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {b64_credentials}",
        }

    async def _request(self, endpoint: str) -> dict[str, Any]:
        """Make API request."""
        url = f"{API_BASE_URL}{endpoint}"

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self.session.get(url, headers=self.headers) as response:
                    if response.status == 401:
                        raise UpdateFailed("Authentication failed")
                    if response.status == 408:
                        raise UpdateFailed("Device timeout - may be offline")
                    if response.status != 200:
                        raise UpdateFailed(f"HTTP {response.status}")

                    return await response.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except TimeoutError as err:
            raise UpdateFailed("Request timeout") from err

    async def get_hub_status(self) -> dict[str, Any]:
        """Get hub status including connected devices."""
        return await self._request(f"/api/device/status/{self.hub_id}")

    async def get_battery_details(self, battery_id: str | None = None) -> dict[str, Any]:
        """Get battery details."""
        endpoint = f"/api/device/battery/{self.hub_id}"
        if battery_id:
            endpoint += f"?id={battery_id}"
        return await self._request(endpoint)

    async def get_meter_details(self, meter_id: str | None = None) -> dict[str, Any]:
        """Get meter details."""
        endpoint = f"/api/device/meter/{self.hub_id}"
        if meter_id:
            endpoint += f"?id={meter_id}"
        return await self._request(endpoint)

    async def get_mode(self) -> str:
        """Get current operating mode."""
        data = await self._request(f"/api/device/{self.hub_id}/mode")
        return data.get("mode", "UNKNOWN")

    async def get_schedule(self) -> dict[str, Any]:
        """Get schedule."""
        return await self._request(f"/api/device/schedule/{self.hub_id}")

    async def set_override(self, mode: str, timeout: int) -> dict[str, Any]:
        """Set mode override."""
        url = f"{API_BASE_URL}/api/device/{self.hub_id}/override?mode={mode}&timeout={timeout}"

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self.session.put(url, headers=self.headers) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"Override failed: HTTP {response.status}")
                    return await response.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Override error: {err}") from err

    async def get_config(self, key: str) -> dict[str, Any]:
        """Get configuration value."""
        return await self._request(f"/api/device/config/{self.hub_id}/{key}")

    async def set_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Set configuration values."""
        url = f"{API_BASE_URL}/api/device/config/{self.hub_id}"

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self.session.put(url, headers=self.headers, json=config) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"Config failed: HTTP {response.status}")
                    return await response.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Config error: {err}") from err

    async def get_device_metadata(self) -> list[dict[str, Any]]:
        """Return per-device metadata (alias, meterPurpose) for the hub.

        Calls the installer-gui user endpoint, which returns:
        ``{"success": true, "metadata": [{"friendlyId", "alias", "meterPurpose"}]}``.
        """
        data = await self._request(f"/api/user/device-metadata/{self.hub_id}")
        return data.get("metadata", []) or []

    async def get_telemetry(self, device_id: str, from_dt: datetime, to_dt: datetime) -> dict[str, Any]:
        """Get energy telemetry for a device over a time range."""
        from_str = from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_str = to_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return await self._request(
            f"/api/device/telemetry/{device_id}?from={from_str}&to={to_str}"
        )


class AnodeStatusCoordinator(DataUpdateCoordinator):
    """Coordinator for hub status and device discovery."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AnodeAPIClient,
        update_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        self.api_client = api_client

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_status",
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch hub status data and merge in per-device metadata."""
        try:
            status = await self.api_client.get_hub_status()
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching hub status: {err}") from err

        # Fail-soft metadata fetch: older hubs / backends may not expose this
        # endpoint. If it errors, proceed with meterPurpose = None so the rest
        # of the integration keeps working.
        purpose_by_id: dict[str, str | None] = {}
        alias_by_id: dict[str, str | None] = {}
        try:
            metadata = await self.api_client.get_device_metadata()
            for item in metadata:
                fid = item.get("friendlyId")
                if not fid:
                    continue
                purpose_by_id[fid] = item.get("meterPurpose")
                alias_by_id[fid] = item.get("alias")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Device metadata unavailable: %s", err)

        for meter in status.get("meter", []) or []:
            mid = meter.get("id")
            meter["meterPurpose"] = purpose_by_id.get(mid)
            if alias_by_id.get(mid) is not None:
                meter["alias"] = alias_by_id.get(mid)

        for battery in status.get("battery", []) or []:
            bid = battery.get("id")
            if alias_by_id.get(bid) is not None:
                battery["alias"] = alias_by_id.get(bid)

        hub_alias = alias_by_id.get(self.api_client.hub_id)
        if hub_alias is not None:
            status.setdefault("hub", {})["alias"] = hub_alias

        return status


class AnodeDeviceCoordinator(DataUpdateCoordinator):
    """Coordinator for individual device data (batteries and meters)."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AnodeAPIClient,
        update_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        self.api_client = api_client
        self._battery_ids: list[str] = []
        self._meter_ids: list[str] = []

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_devices",
            update_interval=timedelta(seconds=update_interval),
        )

    def set_device_ids(self, battery_ids: list[str], meter_ids: list[str]) -> None:
        """Update the list of devices to poll."""
        self._battery_ids = battery_ids
        self._meter_ids = meter_ids

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device data for all batteries and meters."""
        data: dict[str, Any] = {
            "batteries": {},
            "meters": {},
        }

        # Fetch battery data
        for battery_id in self._battery_ids:
            try:
                battery_data = await self.api_client.get_battery_details(battery_id)
                data["batteries"][battery_id] = battery_data
            except UpdateFailed as err:
                _LOGGER.warning("Failed to update battery %s: %s", battery_id, err)

        # Fetch meter data
        for meter_id in self._meter_ids:
            try:
                meter_data = await self.api_client.get_meter_details(meter_id)
                data["meters"][meter_id] = meter_data
            except UpdateFailed as err:
                _LOGGER.warning("Failed to update meter %s: %s", meter_id, err)

        return data


class AnodeModeCoordinator(DataUpdateCoordinator):
    """Coordinator for mode and schedule data with smart refresh."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AnodeAPIClient,
    ) -> None:
        """Initialize the coordinator."""
        self.api_client = api_client
        self._next_schedule_time: datetime | None = None

        # Start with a reasonable interval, will be adjusted based on schedule
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_mode",
            update_interval=timedelta(seconds=60),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch mode and schedule data."""
        mode = await self.api_client.get_mode()
        schedule_data = await self.api_client.get_schedule()

        # Calculate next schedule transition
        next_mode, next_time = self._calculate_next_schedule(schedule_data.get("schedule", []))
        self._next_schedule_time = next_time

        # Adjust update interval based on next schedule time
        self._adjust_update_interval(next_time)

        return {
            "mode": mode,
            "schedule": schedule_data.get("schedule", []),
            "next_mode": next_mode,
            "next_time": next_time,
        }

    def _calculate_next_schedule(
        self, schedule: list[dict[str, Any]]
    ) -> tuple[str | None, datetime | None]:
        """Calculate the next scheduled mode transition."""
        if not schedule:
            return None, None

        now = dt_util.now()
        current_time = now.time()
        today_date = now.date()

        # Convert schedule slots to datetime objects for today
        transitions: list[tuple[datetime, str]] = []

        for slot in schedule:
            begin = slot.get("begin", {})
            mode = slot.get("mode", "UNKNOWN")

            begin_time = time(
                hour=begin.get("hour", 0),
                minute=begin.get("minute", 0),
                second=begin.get("second", 0),
            )
            # Create timezone-aware datetime
            begin_datetime = dt_util.as_local(datetime.combine(today_date, begin_time))

            # If time has passed today, schedule for tomorrow
            if begin_datetime <= now:
                begin_datetime = dt_util.as_local(
                    datetime.combine(today_date + timedelta(days=1), begin_time)
                )

            transitions.append((begin_datetime, mode))

        # Sort by time and get the earliest
        if transitions:
            transitions.sort(key=lambda x: x[0])
            next_time, next_mode = transitions[0]
            return next_mode, next_time

        return None, None

    def _adjust_update_interval(self, next_time: datetime | None) -> None:
        """Adjust update interval based on next schedule time."""
        if next_time is None:
            # No schedule, use default 5 minute interval
            self.update_interval = timedelta(minutes=5)
            return

        now = dt_util.now()
        time_until_transition = (next_time - now).total_seconds()

        if time_until_transition < 60:
            # Less than 1 minute away, update every 10 seconds
            self.update_interval = timedelta(seconds=10)
        elif time_until_transition < 300:
            # Less than 5 minutes away, update every 30 seconds
            self.update_interval = timedelta(seconds=30)
        elif time_until_transition < 1800:
            # Less than 30 minutes away, update every minute
            self.update_interval = timedelta(minutes=1)
        else:
            # More than 30 minutes away, update every 5 minutes
            self.update_interval = timedelta(minutes=5)

    async def async_request_refresh_soon(self) -> None:
        """Request a refresh soon (used after override changes)."""
        # Force immediate refresh
        await self.async_request_refresh()


class AnodeEnergyCoordinator(DataUpdateCoordinator):
    """Coordinator for energy telemetry (delta since last update)."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AnodeAPIClient,
    ) -> None:
        """Initialize the coordinator."""
        self.api_client = api_client
        self._battery_ids: list[str] = []
        self._meter_ids: list[str] = []
        self._last_update_time: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_energy",
            update_interval=timedelta(seconds=60),
        )

    def set_device_ids(self, battery_ids: list[str], meter_ids: list[str]) -> None:
        """Update the list of devices to poll."""
        self._battery_ids = battery_ids
        self._meter_ids = meter_ids

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch energy telemetry delta since last successful update."""
        now = dt_util.utcnow()

        if self._last_update_time is None:
            # First run: use a short lookback to seed initial delta
            from_dt = now - timedelta(minutes=5)
        else:
            from_dt = self._last_update_time
            # Clamp to API max of 24 hours
            if (now - from_dt) > timedelta(hours=24):
                from_dt = now - timedelta(hours=24)

        data: dict[str, Any] = {
            "batteries": {},
            "meters": {},
        }

        for battery_id in self._battery_ids:
            try:
                result = await self.api_client.get_telemetry(battery_id, from_dt, now)
                # Telemetry API already converts dWh→Wh internally; values are in Wh
                data["batteries"][battery_id] = {
                    "import_wh": result.get("import", 0.0),
                    "export_wh": result.get("export", 0.0),
                }
            except UpdateFailed as err:
                _LOGGER.debug("Failed to get energy telemetry for battery %s: %s", battery_id, err)

        for meter_id in self._meter_ids:
            try:
                result = await self.api_client.get_telemetry(meter_id, from_dt, now)
                # Telemetry API already converts dWh→Wh internally; values are in Wh
                data["meters"][meter_id] = {
                    "import_wh": result.get("import", 0.0),
                    "export_wh": result.get("export", 0.0),
                }
            except UpdateFailed as err:
                _LOGGER.debug("Failed to get energy telemetry for meter %s: %s", meter_id, err)

        self._last_update_time = now
        return data

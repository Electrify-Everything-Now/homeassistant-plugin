"""Async client for the Anode cloud API."""
from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import aiohttp

from .exceptions import (
    AnodeAuthError,
    AnodeConnectionError,
    AnodeError,
    AnodeForbiddenError,
    AnodeHubOfflineError,
    AnodeNotFoundError,
    AnodeRateLimitError,
    AnodeResponseError,
)
from .models import (
    AccountHub,
    BatteryReading,
    DeviceMetadata,
    HubStatus,
    MeterReading,
    OperatingMode,
    PowerLimit,
    PowerLimitKey,
    ScheduleSlot,
    SocLimits,
    parse_account_hub,
    parse_batteries,
    parse_device_metadata,
    parse_meters,
    parse_power_limit,
    parse_schedule,
    parse_soc_limits,
    raise_for_hub_failure,
    require_dict,
)

# Endpoint paths below are relative to this. The /api suffix is due to be
# dropped from the host, at which point only this line needs to change.
API_BASE_URL = "https://api.anode.energy/api"
DEFAULT_TIMEOUT = 30.0

_STATUS_ERRORS: dict[int, type[AnodeError]] = {
    HTTPStatus.UNAUTHORIZED: AnodeAuthError,
    HTTPStatus.FORBIDDEN: AnodeForbiddenError,
    HTTPStatus.NOT_FOUND: AnodeNotFoundError,
    HTTPStatus.REQUEST_TIMEOUT: AnodeHubOfflineError,
    HTTPStatus.TOO_MANY_REQUESTS: AnodeRateLimitError,
}


def _hub(hub_id: str) -> str:
    return quote(hub_id, safe="")


class AnodeClient:
    """Client for one Anode account.

    Authenticates with HTTP Basic auth: the account email is the username and
    an API key from the Anode web app is the password.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        api_key: str,
        *,
        base_url: str = API_BASE_URL,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._auth = aiohttp.BasicAuth(email, api_key)
        self._base_url = base_url.rstrip("/")
        self._timeout = request_timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
    ) -> Any:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method,
                    f"{self._base_url}{path}",
                    auth=self._auth,
                    params=params,
                    json=json,
                ) as response:
                    if error := _STATUS_ERRORS.get(response.status):
                        raise error(f"{method} {path} returned HTTP {response.status}")
                    if response.status >= 400:
                        raise AnodeResponseError(
                            f"{method} {path} returned HTTP {response.status}"
                        )
                    try:
                        return await response.json(content_type=None)
                    except ValueError as err:
                        raise AnodeResponseError(f"{method} {path} returned invalid JSON") from err
        except AnodeError:
            raise
        except TimeoutError as err:
            raise AnodeConnectionError(f"{method} {path} timed out") from err
        except aiohttp.ClientError as err:
            raise AnodeConnectionError(f"{method} {path} failed: {err}") from err

    async def get_account_hub(self) -> AccountHub:
        """Return the hub linked to this account.

        Raises AnodeNotFoundError when the account has no hub of its own,
        for example an installer account.
        """
        return parse_account_hub(await self._request("GET", "/user/devices"))

    async def get_hub_status(self, hub_id: str) -> HubStatus:
        """Return hub status and the batteries and meters paired to it."""
        data = await self._request("GET", f"/device/status/{_hub(hub_id)}")
        return HubStatus.from_api(hub_id, data)

    async def get_device_metadata(self, hub_id: str) -> dict[str, DeviceMetadata]:
        """Return web-UI aliases and meter purposes, keyed by device id."""
        data = await self._request("GET", f"/user/device-metadata/{_hub(hub_id)}")
        return parse_device_metadata(data)

    async def get_batteries(self, hub_id: str) -> dict[str, BatteryReading]:
        """Return readings for every battery the hub is using, in one request."""
        return parse_batteries(await self._request("GET", f"/device/battery/{_hub(hub_id)}"))

    async def get_battery(self, hub_id: str, battery_id: str) -> BatteryReading:
        """Return readings for one battery.

        Current firmware includes BMS data here but not in ``get_batteries``.
        """
        data = await self._request(
            "GET", f"/device/battery/{_hub(hub_id)}", params={"id": battery_id}
        )
        data = require_dict(data, f"battery {battery_id}")
        raise_for_hub_failure(data, f"the battery {battery_id} read")
        return BatteryReading.from_api(data)

    async def get_meters(self, hub_id: str) -> dict[str, MeterReading]:
        """Return readings for every meter the hub is using, in one request."""
        return parse_meters(await self._request("GET", f"/device/meter/{_hub(hub_id)}"))

    async def get_mode(self, hub_id: str) -> OperatingMode | None:
        """Return the mode the hub is running now, or None if unrecognised."""
        data = require_dict(await self._request("GET", f"/device/{_hub(hub_id)}/mode"), "mode")
        raw = data.get("mode")
        try:
            return OperatingMode(raw.strip().upper()) if isinstance(raw, str) else None
        except ValueError:
            return None

    async def get_schedule(self, hub_id: str) -> list[ScheduleSlot]:
        """Return the hub schedule."""
        return parse_schedule(await self._request("GET", f"/device/schedule/{_hub(hub_id)}"))

    async def _get_config_value(self, hub_id: str, key: str) -> Any:
        data = await self._request("GET", f"/device/config/{_hub(hub_id)}/{quote(key, safe='')}")
        if isinstance(data, list):
            return data
        data = require_dict(data, f"config {key}")
        raise_for_hub_failure(data, f"the {key} read")
        if "value" not in data:
            raise AnodeResponseError(f"Config response for {key} has no value")
        return data["value"]

    async def _set_config(self, hub_id: str, payload: dict[str, Any]) -> None:
        data = await self._request("PUT", f"/device/config/{_hub(hub_id)}", json=payload)
        raise_for_hub_failure(data, "the configuration change")

    async def get_soc_limits(self, hub_id: str) -> dict[str, SocLimits]:
        """Return min/max state of charge per battery."""
        return parse_soc_limits(await self._get_config_value(hub_id, "socConfig"))

    async def set_soc_limits(self, hub_id: str, battery_id: str, limits: SocLimits) -> None:
        """Set the state-of-charge window for one battery."""
        if not 0 <= limits.min_soc <= limits.max_soc <= 100:
            raise ValueError("SOC limits must satisfy 0 <= min <= max <= 100")
        await self._set_config(
            hub_id,
            {f"socConfig_{battery_id}": {"minSoc": limits.min_soc, "maxSoc": limits.max_soc}},
        )

    async def get_power_limit(self, hub_id: str, key: PowerLimitKey) -> PowerLimit:
        """Return a fleet charge or discharge power limit."""
        return parse_power_limit(await self._get_config_value(hub_id, key.value))

    async def set_power_limit_percent(self, hub_id: str, key: PowerLimitKey, percent: int) -> None:
        """Set a power limit as a percent of fleet power (current firmware)."""
        await self._set_config(hub_id, {key.value: {"percent": int(percent)}})

    async def set_power_limit_watts(
        self, hub_id: str, key: PowerLimitKey, watts: int, *, percent_based: bool
    ) -> None:
        """Set a power limit in watts.

        Percent-based firmware snaps watts to the nearest percent; older
        firmware only understands a bare number.
        """
        value: Any = {"watts": int(watts)} if percent_based else int(watts)
        await self._set_config(hub_id, {key.value: value})

    async def set_override(self, hub_id: str, mode: OperatingMode, duration_s: int) -> None:
        """Run the hub in a mode for a number of seconds, ignoring its schedule."""
        if duration_s < 0:
            raise ValueError("Override duration must be zero or positive")
        data = await self._request(
            "PUT",
            f"/device/{_hub(hub_id)}/override",
            params={"mode": mode.value, "timeout": str(int(duration_s))},
        )
        raise_for_hub_failure(data, "the override")

    async def cancel_override(self, hub_id: str) -> None:
        """End any active override so the hub follows its schedule again."""
        await self.set_override(hub_id, OperatingMode.MATCH, 0)

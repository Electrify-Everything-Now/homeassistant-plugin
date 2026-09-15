"""Shared helpers for Anode tests."""
from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
from typing import Any

from yarl import URL

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.anode_battery.api import API_BASE_URL
from custom_components.anode_battery.const import DOMAIN

HUB_ID = "ehxbt"
EMAIL = "owner@example.com"
API_KEY = "test-api-key"

FIXTURES = Path(__file__).parent / "fixtures"

DEFAULT_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("GET", "/user/devices", "account_devices.json"),
    ("GET", f"/device/status/{HUB_ID}", "status.json"),
    ("GET", f"/user/device-metadata/{HUB_ID}", "device_metadata.json"),
    ("GET", f"/device/battery/{HUB_ID}", "batteries.json"),
    ("GET", f"/device/meter/{HUB_ID}", "meters.json"),
    ("GET", f"/device/{HUB_ID}/mode", "mode.json"),
    ("GET", f"/device/schedule/{HUB_ID}", "schedule.json"),
    ("GET", f"/device/config/{HUB_ID}/socConfig", "config_soc.json"),
    ("GET", f"/device/config/{HUB_ID}/maxChargePower", "config_max_charge_power.json"),
    ("GET", f"/device/config/{HUB_ID}/maxDischargePower", "config_max_discharge_power.json"),
    ("PUT", f"/device/config/{HUB_ID}", "hub_ack.json"),
    ("PUT", f"/device/{HUB_ID}/override", "override_ack.json"),
)

STATUS = f"/device/status/{HUB_ID}"
METADATA = f"/user/device-metadata/{HUB_ID}"
BATTERIES = f"/device/battery/{HUB_ID}"
METERS = f"/device/meter/{HUB_ID}"
MODE = f"/device/{HUB_ID}/mode"
SCHEDULE = f"/device/schedule/{HUB_ID}"
SOC_CONFIG = f"/device/config/{HUB_ID}/socConfig"
MAX_CHARGE = f"/device/config/{HUB_ID}/maxChargePower"
MAX_DISCHARGE = f"/device/config/{HUB_ID}/maxDischargePower"
SET_CONFIG = f"/device/config/{HUB_ID}"
OVERRIDE = f"/device/{HUB_ID}/override"
ACCOUNT = "/user/devices"


def load_fixture(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text())


class AnodeCloud:
    """A fake Anode cloud serving fixture responses over mocked HTTP.

    Tests change a response with ``respond`` and inspect what was sent with
    ``calls``.
    """

    def __init__(self, aioclient_mock: AiohttpClientMocker) -> None:
        self._mock = aioclient_mock
        self._routes: dict[tuple[str, str], dict[str, Any]] = {}
        self.requests: list[tuple[str, URL, Any]] = []
        for method, path, fixture in DEFAULT_ROUTES:
            self.respond(method, path, json=load_fixture(fixture))

    def respond(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        text: str | None = None,
        status: int = HTTPStatus.OK,
        exc: BaseException | None = None,
    ) -> None:
        """Set the response for an endpoint."""
        key = (method.upper(), path)
        if key not in self._routes:
            self._mock.request(method, f"{API_BASE_URL}{path}", side_effect=self._handler(key))
        self._routes[key] = {"json": json, "text": text, "status": status, "exc": exc}

    def _handler(self, key: tuple[str, str]):
        async def handle(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
            self.requests.append((method.upper(), url, data))
            route = self._routes[key]
            return AiohttpClientMockResponse(
                method,
                url,
                status=route["status"],
                json=route["json"],
                text=route["text"],
                exc=route["exc"],
            )

        return handle

    def calls(self, method: str, path: str) -> list[tuple[URL, Any]]:
        """Requests made to an endpoint, as (url, body)."""
        full_path = URL(f"{API_BASE_URL}{path}").path
        return [
            (url, body)
            for sent_method, url, body in self.requests
            if sent_method == method.upper() and url.path == full_path
        ]


def entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    """Return the entity id registered for a unique id."""
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert found, f"no {platform} entity with unique id {unique_id}"
    return found


def state(hass: HomeAssistant, platform: str, unique_id: str) -> State:
    """Return the state of the entity with a unique id."""
    current = hass.states.get(entity_id(hass, platform, unique_id))
    assert current is not None
    return current


async def refresh(hass: HomeAssistant, coordinator: DataUpdateCoordinator) -> None:
    """Refresh a coordinator and let entities catch up."""
    await coordinator.async_refresh()
    await hass.async_block_till_done()

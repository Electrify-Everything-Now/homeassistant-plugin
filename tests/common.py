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

ACCOUNT = "/user/devices"
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

DEFAULT_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("GET", ACCOUNT, "account_devices.json"),
    ("GET", STATUS, "status.json"),
    ("GET", METADATA, "device_metadata.json"),
    ("GET", BATTERIES, "batteries.json"),
    ("GET", METERS, "meters.json"),
    ("GET", MODE, "mode.json"),
    ("GET", SCHEDULE, "schedule.json"),
    ("GET", SOC_CONFIG, "config_soc.json"),
    ("GET", MAX_CHARGE, "config_max_charge_power.json"),
    ("GET", MAX_DISCHARGE, "config_max_discharge_power.json"),
    ("PUT", SET_CONFIG, "hub_ack.json"),
    ("PUT", OVERRIDE, "override_ack.json"),
)

# Single-battery reads (?id=...). Only bat01's firmware reports BMS data.
BATTERY_ROUTES: dict[str, str] = {
    "bat01": "battery_bat01.json",
    "bat02": "battery_bat02.json",
}


def load_fixture(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text())


class AnodeCloud:
    """A fake Anode cloud serving fixture responses over mocked HTTP.

    Tests change a response with ``respond`` and inspect what was sent with
    ``calls``. A response can be limited to one query string, such as a
    single battery's ``?id=``; other queries get the endpoint's default.
    """

    def __init__(self, aioclient_mock: AiohttpClientMocker) -> None:
        self._mock = aioclient_mock
        self._registered: set[tuple[str, str]] = set()
        self._routes: dict[tuple[str, str, frozenset[tuple[str, str]]], dict[str, Any]] = {}
        self.requests: list[tuple[str, URL, Any]] = []
        for method, path, fixture in DEFAULT_ROUTES:
            self.respond(method, path, json=load_fixture(fixture))
        for battery_id, fixture in BATTERY_ROUTES.items():
            self.respond("GET", BATTERIES, query={"id": battery_id}, json=load_fixture(fixture))

    def respond(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        json: Any = None,
        text: str | None = None,
        status: int = HTTPStatus.OK,
        exc: BaseException | None = None,
    ) -> None:
        """Set the response for an endpoint, optionally for one query only."""
        method = method.upper()
        if (method, path) not in self._registered:
            self._registered.add((method, path))
            self._mock.request(method, f"{API_BASE_URL}{path}", side_effect=self._handler(method, path))
        self._routes[(method, path, frozenset((query or {}).items()))] = {
            "json": json,
            "text": text,
            "status": status,
            "exc": exc,
        }

    def _handler(self, method: str, path: str):
        async def handle(sent_method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
            self.requests.append((sent_method.upper(), url, data))
            route = self._routes.get((method, path, frozenset(url.query.items())))
            if route is None:
                route = self._routes[(method, path, frozenset())]
            return AiohttpClientMockResponse(
                sent_method,
                url,
                status=route["status"],
                json=route["json"],
                text=route["text"],
                exc=route["exc"],
            )

        return handle

    def calls(
        self, method: str, path: str, *, query: dict[str, str] | None = None
    ) -> list[tuple[URL, Any]]:
        """Requests made to an endpoint, as (url, body); optionally one query only."""
        full_path = URL(f"{API_BASE_URL}{path}").path
        return [
            (url, body)
            for sent_method, url, body in self.requests
            if sent_method == method.upper()
            and url.path == full_path
            and (query is None or dict(url.query) == query)
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

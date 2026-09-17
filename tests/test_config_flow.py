"""Tests for the Anode config flow: connection menu, API keys and options."""
from __future__ import annotations

from collections.abc import Generator
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.const import (
    AUTH_API_KEY,
    CONF_AUTH_TYPE,
    CONF_DEVICE_INTERVAL,
    CONF_HUB_ID,
    CONF_STATUS_INTERVAL,
    DOMAIN,
)

from .common import (
    ACCOUNT,
    API_KEY,
    DEFAULT_ROUTES,
    EMAIL,
    HUB_ID,
    STATUS,
    AnodeCloud,
    load_fixture,
)

CREDENTIALS = {CONF_EMAIL: f" {EMAIL} ", CONF_API_KEY: API_KEY}


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop created entries from being set up."""
    with patch(
        "custom_components.anode_battery.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


def restore_default(cloud: AnodeCloud, route: str) -> None:
    fixture = next(name for method, path, name in DEFAULT_ROUTES if (method, path) == ("GET", route))
    cloud.respond("GET", route, json=load_fixture(fixture))


async def open_api_key_form(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "api_key"}
    )


async def test_connection_menu(hass: HomeAssistant) -> None:
    """Setup starts by choosing how to connect."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["link", "api_key"]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_api_key_finds_account_hub(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Credentials are enough when the account owns a hub."""
    result = await open_api_key_form(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "api_key"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home hub"
    assert result["data"] == {
        CONF_AUTH_TYPE: AUTH_API_KEY,
        CONF_EMAIL: EMAIL,
        CONF_API_KEY: API_KEY,
        CONF_HUB_ID: HUB_ID,
    }
    assert result["result"].unique_id == HUB_ID


@pytest.mark.usefixtures("mock_setup_entry")
async def test_installer_account_enters_hub(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Accounts without a hub are asked for its ID."""
    cloud.respond("GET", ACCOUNT, status=HTTPStatus.NOT_FOUND, json={"message": "No hub"})
    result = await open_api_key_form(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hub"

    cloud.respond("GET", STATUS, status=HTTPStatus.FORBIDDEN)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HUB_ID: " EHXBT "}
    )
    assert result["errors"] == {"base": "hub_not_found"}

    restore_default(cloud, STATUS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HUB_ID: " EHXBT "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Anode Hub {HUB_ID}"
    assert result["data"][CONF_HUB_ID] == HUB_ID
    assert result["data"][CONF_AUTH_TYPE] == AUTH_API_KEY


@pytest.mark.usefixtures("mock_setup_entry")
@pytest.mark.parametrize(
    ("route", "response", "error"),
    [
        (ACCOUNT, {"status": HTTPStatus.UNAUTHORIZED}, "invalid_auth"),
        (ACCOUNT, {"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        (ACCOUNT, {"status": HTTPStatus.BAD_GATEWAY}, "cannot_connect"),
        (STATUS, {"status": HTTPStatus.REQUEST_TIMEOUT}, "hub_offline"),
    ],
)
async def test_api_key_errors_recover(
    hass: HomeAssistant,
    cloud: AnodeCloud,
    route: str,
    response: dict[str, Any],
    error: str,
) -> None:
    """Errors are shown on the form and the flow can still finish."""
    result = await open_api_key_form(hass)

    cloud.respond("GET", route, **response)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    restore_default(cloud, route)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_api_key_unexpected_error(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Unexpected exceptions show a generic error."""
    result = await open_api_key_form(hass)
    with patch(
        "custom_components.anode_battery.config_flow.AnodeClient.get_account_hub",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["errors"] == {"base": "unknown"}


async def test_api_key_already_configured(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A hub can only be added once."""
    mock_config_entry.add_to_hass(hass)
    result = await open_api_key_form(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_with_api_key(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A new API key can be entered after the old one is rejected."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "api_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "api_key"

    cloud.respond("GET", STATUS, status=HTTPStatus.UNAUTHORIZED)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_API_KEY: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    restore_default(cloud, STATUS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_API_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {
        CONF_AUTH_TYPE: AUTH_API_KEY,
        CONF_EMAIL: EMAIL,
        CONF_API_KEY: "new-key",
        CONF_HUB_ID: HUB_ID,
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_with_api_key(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Credentials can be changed without removing the integration."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "api_key"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "new@example.com", CONF_API_KEY: "rotated"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {
        CONF_AUTH_TYPE: AUTH_API_KEY,
        CONF_EMAIL: "new@example.com",
        CONF_API_KEY: "rotated",
        CONF_HUB_ID: HUB_ID,
    }


async def test_options_flow(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Polling intervals can be changed."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STATUS_INTERVAL: 60, CONF_DEVICE_INTERVAL: 15}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_STATUS_INTERVAL: 60, CONF_DEVICE_INTERVAL: 15}
    assert init_integration.runtime_data.telemetry.update_interval.total_seconds() == 15

"""Tests for linking an Anode account, where the key is issued on approval."""
from __future__ import annotations

from collections.abc import Generator
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.api import LINK_CLIENT_KIND
from custom_components.anode_battery.const import (
    AUTH_LINK,
    CONF_AUTH_TYPE,
    CONF_HUB_ID,
    CONF_READ_ONLY,
    DOMAIN,
)

from .common import (
    ACCOUNT,
    EMAIL,
    HUB_ID,
    LINK_REQUEST,
    LINK_TOKEN,
    AnodeCloud,
    load_fixture,
)

# The key the fake cloud issues on approval, which is never the one in the
# entry a test starts from.
LINKED_KEY = "linked-api-key"


@pytest.fixture(autouse=True)
def no_poll_delay() -> Generator[None]:
    """Poll without waiting, so a flow finishes in the time a test has."""
    with patch("custom_components.anode_battery.config_flow.LINK_MIN_POLL_INTERVAL", 0):
        yield


@pytest.fixture(autouse=True)
def instant_code(cloud: AnodeCloud) -> None:
    """A code the fake cloud says to poll for with no interval."""
    cloud.respond("POST", LINK_REQUEST, json=load_fixture("link_request.json") | {"interval": 0})


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop created entries from being set up."""
    with patch(
        "custom_components.anode_battery.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


def approved(**changes: Any) -> dict[str, Any]:
    """The approved token response, with fields changed."""
    return load_fixture("link_approved.json") | changes


async def run_link(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """Choose linking and let the polling task run to its conclusion."""
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "link"})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "wait_for_link"

    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(flow_id)
    if result["type"] is FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(flow_id)
    return result


async def start_link(hass: HomeAssistant) -> dict[str, Any]:
    """Add the integration and choose linking."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await run_link(hass, result["flow_id"])


@pytest.mark.usefixtures("mock_setup_entry")
async def test_link_creates_entry(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """An approved link sets the integration up with the key it was issued."""
    result = await start_link(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home hub"
    assert result["data"] == {
        CONF_AUTH_TYPE: AUTH_LINK,
        CONF_EMAIL: EMAIL,
        CONF_API_KEY: LINKED_KEY,
        CONF_HUB_ID: HUB_ID,
        CONF_READ_ONLY: False,
    }
    assert result["result"].unique_id == HUB_ID


@pytest.mark.usefixtures("mock_setup_entry")
async def test_link_asks_for_the_scopes_it_needs(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """The request names this client and asks for one hub's reads and control."""
    await start_link(hass)

    (_, body), *rest = cloud.calls("POST", LINK_REQUEST)
    assert not rest
    assert body["clientKind"] == LINK_CLIENT_KIND
    assert body["clientName"].startswith("Home Assistant")
    assert sorted(body["scopes"]) == ["device:control", "device:read"]
    assert body["binding"] == "hub"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_link_polls_until_approved(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Waiting is normal: the flow keeps polling until a human decides."""
    cloud.respond(
        "POST",
        LINK_TOKEN,
        sequence=[
            {"json": {"status": "pending"}},
            {"json": {"status": "pending"}},
            {"json": approved()},
        ],
    )

    result = await start_link(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(cloud.calls("POST", LINK_TOKEN)) == 3


@pytest.mark.usefixtures("mock_setup_entry")
async def test_link_survives_a_failed_poll(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """A rate-limited or unreadable poll is retried; the code is still good."""
    cloud.respond(
        "POST",
        LINK_TOKEN,
        sequence=[
            {"status": HTTPStatus.TOO_MANY_REQUESTS, "json": {"message": "slow down"}},
            {"json": approved()},
        ],
    )

    result = await start_link(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_link_denied(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Refusing in the web app stops the flow."""
    cloud.respond("POST", LINK_TOKEN, json={"status": "denied"})

    result = await start_link(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_denied"


async def test_link_expired_offers_another_way(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """A code nobody approved leads back to the choice of how to connect."""
    cloud.respond(
        "POST", LINK_TOKEN, status=HTTPStatus.NOT_FOUND, json={"status": "expired"}
    )

    result = await start_link(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "link_expired"
    assert result["menu_options"] == ["link", "api_key"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "api_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "api_key"


async def test_link_rate_limited(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Asking for too many codes is its own message, not a connection error."""
    cloud.respond(
        "POST", LINK_REQUEST, status=HTTPStatus.TOO_MANY_REQUESTS, json={"message": "slow down"}
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "link"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_rate_limited"


async def test_link_request_fails(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """A cloud that will not issue a code is a connection problem."""
    cloud.respond("POST", LINK_REQUEST, status=HTTPStatus.INTERNAL_SERVER_ERROR, json={})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "link"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_link_without_control_is_read_only(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """A grant that only reads is kept, with the controls left out."""
    cloud.respond("POST", LINK_TOKEN, json=approved(scopes=["device:read"]))

    result = await start_link(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_READ_ONLY] is True


async def test_link_without_read_is_refused(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """A key that cannot even read is not worth storing."""
    cloud.respond("POST", LINK_TOKEN, json=approved(scopes=[]))

    result = await start_link(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_insufficient"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_link_account_bound_grant_finds_its_hub(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """A grant naming no hub is asked what it reaches."""
    cloud.respond("POST", LINK_TOKEN, json=approved(hubId=None, binding="account"))

    result = await start_link(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HUB_ID] == HUB_ID


async def test_link_account_bound_grant_with_no_hub_to_find(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """A grant naming no hub, on an account with none, has nothing to set up."""
    cloud.respond("POST", LINK_TOKEN, json=approved(hubId=None, binding="account"))
    cloud.respond("GET", ACCOUNT, status=HTTPStatus.NOT_FOUND, json={"message": "No hub"})

    result = await start_link(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_approval_that_cannot_be_read_is_not_retried(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """Collecting spends the code, so an unreadable approval stops rather than polls."""
    cloud.respond("POST", LINK_TOKEN, json=approved(scopes="device:read"))

    result = await start_link(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_failed"
    assert len(cloud.calls("POST", LINK_TOKEN)) == 1


async def test_link_already_configured(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A hub can only be added once, however it was connected."""
    mock_config_entry.add_to_hass(hass)

    result = await start_link(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_by_linking(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Linking again replaces the credentials Anode stopped accepting."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.MENU

    result = await run_link(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {
        CONF_AUTH_TYPE: AUTH_LINK,
        CONF_EMAIL: EMAIL,
        CONF_API_KEY: LINKED_KEY,
        CONF_HUB_ID: HUB_ID,
        CONF_READ_ONLY: False,
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_by_linking(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """An entry set up with an API key can be moved onto a linked key."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU

    result = await run_link(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_AUTH_TYPE] == AUTH_LINK
    assert mock_config_entry.data[CONF_API_KEY] == LINKED_KEY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_to_another_hub_is_refused(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Approving the wrong hub must not repoint an entry at it."""
    mock_config_entry.add_to_hass(hass)
    cloud.respond("POST", LINK_TOKEN, json=approved(hubId="other"))

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await run_link(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hub"
    assert mock_config_entry.data[CONF_HUB_ID] == HUB_ID

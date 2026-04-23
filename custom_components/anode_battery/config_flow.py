"""Config flow for Anode integration."""
from __future__ import annotations

import logging
from typing import Any
import base64

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

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
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    email = data[CONF_EMAIL]
    api_key = data[CONF_API_KEY]
    hub_id = data[CONF_HUB_ID]

    # Create Basic Auth header
    credentials = f"{email}:{api_key}"
    b64_credentials = base64.b64encode(credentials.encode()).decode()
    headers = {
        "Authorization": f"Basic {b64_credentials}",
    }

    # Test the connection by fetching hub status
    session = async_get_clientsession(hass)
    url = f"{API_BASE_URL}/api/device/status/{hub_id}"

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as response:
            if response.status == 401:
                raise InvalidAuth
            if response.status == 408:
                raise CannotConnect("Hub timeout - device may be offline")
            if response.status != 200:
                raise CannotConnect(f"HTTP {response.status}")

            data = await response.json()

            # Validate response structure
            if "status" not in data or "hub" not in data:
                raise CannotConnect("Invalid response from API")

            # Return hub info for display
            return {
                "title": f"Anode Hub {hub_id}",
                "hub_version": data["hub"].get("version", "unknown"),
            }
    except aiohttp.ClientError as err:
        raise CannotConnect(f"Connection error: {err}") from err
    except TimeoutError as err:
        raise CannotConnect("Connection timeout") from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Anode."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect as err:
                _LOGGER.error("Cannot connect: %s", err)
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Set unique ID based on hub ID to prevent duplicates
                await self.async_set_unique_id(user_input[CONF_HUB_ID])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_HUB_ID): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Anode."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Stash the entry under a private attr.

        We avoid assigning `self.config_entry` because HA ≥ 2024.11 exposes
        it as a read-only property and rejects assignment.
        """
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_STATUS_INTERVAL,
                    default=self._entry.options.get(
                        CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL
                    ),
                ): vol.All(cv.positive_int, vol.Range(min=MIN_UPDATE_INTERVAL)),
                vol.Optional(
                    CONF_DEVICE_INTERVAL,
                    default=self._entry.options.get(
                        CONF_DEVICE_INTERVAL, DEFAULT_DEVICE_INTERVAL
                    ),
                ): vol.All(cv.positive_int, vol.Range(min=MIN_UPDATE_INTERVAL)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""

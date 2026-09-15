"""Config flow for the Anode integration."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    AnodeAuthError,
    AnodeClient,
    AnodeError,
    AnodeForbiddenError,
    AnodeHubOfflineError,
    AnodeNotFoundError,
)
from .const import (
    CONF_DEVICE_INTERVAL,
    CONF_HUB_ID,
    CONF_STATUS_INTERVAL,
    DEFAULT_DEVICE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_UPDATE_INTERVAL,
)
from .coordinator import AnodeConfigEntry

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

HUB_SCHEMA = vol.Schema({vol.Required(CONF_HUB_ID): str})


def _credentials(user_input: Mapping[str, Any]) -> dict[str, str]:
    return {
        CONF_EMAIL: user_input[CONF_EMAIL].strip(),
        CONF_API_KEY: user_input[CONF_API_KEY].strip(),
    }


def _error_key(err: AnodeError) -> str:
    """Map a client error to a config flow error key."""
    if isinstance(err, (AnodeForbiddenError, AnodeNotFoundError)):
        return "hub_not_found"
    if isinstance(err, AnodeAuthError):
        return "invalid_auth"
    if isinstance(err, AnodeHubOfflineError):
        return "hub_offline"
    return "cannot_connect"


class AnodeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Anode."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        self._credentials: dict[str, str] = {}

    def _client(self, credentials: Mapping[str, str]) -> AnodeClient:
        return AnodeClient(
            async_get_clientsession(self.hass),
            credentials[CONF_EMAIL],
            credentials[CONF_API_KEY],
        )

    async def _async_validate_hub(self, credentials: Mapping[str, str], hub_id: str) -> str | None:
        """Return an error key, or None when these credentials can read the hub."""
        try:
            await self._client(credentials).get_hub_status(hub_id)
        except AnodeError as err:
            _LOGGER.debug("Could not read hub %s: %s", hub_id, err)
            return _error_key(err)
        except Exception:
            _LOGGER.exception("Unexpected error reading hub %s", hub_id)
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials, then find the account's hub."""
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = _credentials(user_input)
            try:
                hub = await self._client(credentials).get_account_hub()
            except AnodeNotFoundError:
                # No hub of its own, e.g. an installer account: ask which hub.
                self._credentials = credentials
                return await self.async_step_hub()
            except AnodeError as err:
                errors["base"] = _error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected error looking up the account's hub")
                errors["base"] = "unknown"
            else:
                hub_id = hub.hub_id.lower()
                await self.async_set_unique_id(hub_id)
                self._abort_if_unique_id_configured()
                if (error := await self._async_validate_hub(credentials, hub_id)) is None:
                    return self.async_create_entry(
                        title=hub.alias or f"Anode Hub {hub_id}",
                        data={**credentials, CONF_HUB_ID: hub_id},
                    )
                errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a hub ID when the account has no hub of its own."""
        errors: dict[str, str] = {}
        if user_input is not None:
            hub_id = user_input[CONF_HUB_ID].strip().lower()
            await self.async_set_unique_id(hub_id)
            self._abort_if_unique_id_configured()
            if (error := await self._async_validate_hub(self._credentials, hub_id)) is None:
                return self.async_create_entry(
                    title=f"Anode Hub {hub_id}",
                    data={**self._credentials, CONF_HUB_ID: hub_id},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="hub",
            data_schema=self.add_suggested_values_to_schema(HUB_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the API key was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        return await self._async_step_credentials(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the email or API key."""
        return await self._async_step_credentials(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_step_credentials(
        self,
        step_id: str,
        entry: AnodeConfigEntry,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        hub_id = entry.data[CONF_HUB_ID]
        if user_input is not None:
            credentials = _credentials(user_input)
            if (error := await self._async_validate_hub(credentials, hub_id)) is None:
                return self.async_update_reload_and_abort(entry, data_updates=credentials)
            errors["base"] = error

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, user_input or {CONF_EMAIL: entry.data[CONF_EMAIL]}
            ),
            errors=errors,
            description_placeholders={"hub_id": hub_id},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: AnodeConfigEntry) -> AnodeOptionsFlow:
        """Return the options flow."""
        return AnodeOptionsFlow()


class AnodeOptionsFlow(OptionsFlow):
    """Polling interval options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        interval = vol.All(cv.positive_int, vol.Range(min=MIN_UPDATE_INTERVAL))
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STATUS_INTERVAL,
                    default=options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
                ): interval,
                vol.Required(
                    CONF_DEVICE_INTERVAL,
                    default=options.get(CONF_DEVICE_INTERVAL, DEFAULT_DEVICE_INTERVAL),
                ): interval,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

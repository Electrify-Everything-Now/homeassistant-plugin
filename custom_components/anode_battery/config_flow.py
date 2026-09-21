"""Config flow for the Anode integration."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    QrCodeSelector,
    QrCodeSelectorConfig,
    QrErrorCorrectionLevel,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    SCOPE_DEVICE_READ,
    AccountHub,
    AnodeAuthError,
    AnodeClient,
    AnodeConnectionError,
    AnodeError,
    AnodeForbiddenError,
    AnodeHubOfflineError,
    AnodeLinkDeniedError,
    AnodeLinkExpiredError,
    AnodeNotFoundError,
    AnodeRateLimitError,
    AnodeResponseError,
    LinkCode,
    LinkGrant,
)
from .const import (
    AUTH_API_KEY,
    AUTH_LINK,
    CONF_AUTH_TYPE,
    CONF_DEVICE_INTERVAL,
    CONF_FIRMWARE,
    CONF_HUB_ID,
    CONF_READ_ONLY,
    CONF_STATUS_INTERVAL,
    DEFAULT_DEVICE_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    LINK_MIN_POLL_INTERVAL,
    MIN_UPDATE_INTERVAL,
)
from .coordinator import AnodeConfigEntry

_LOGGER = logging.getLogger(__name__)

# Linking twice over: the plain step waits on its own, and the QR step shows a
# code to scan from a phone first. Approving on a phone is the reason the second
# exists, so it is offered rather than made the only way in.
CONNECTION_MENU = ["link", "link_qr", "api_key"]

# Big enough to scan from a phone held in front of a monitor. Quartile recovery
# because a screen photographed at an angle loses corners.
QR_SCALE = 6

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
    """Handle a config flow for Anode.

    Connects either by linking, where the account holder approves Home
    Assistant in the Anode web app and a key is issued automatically, or with
    an API key created by hand.
    """

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        self._credentials: dict[str, str] = {}
        self._link_code: LinkCode | None = None
        self._link_task: asyncio.Task[LinkGrant] | None = None
        self._grant: LinkGrant | None = None
        self._qr_shown = False

    def _client(self, credentials: Mapping[str, str] | None = None) -> AnodeClient:
        return AnodeClient(
            async_get_clientsession(self.hass),
            credentials[CONF_EMAIL] if credentials else None,
            credentials[CONF_API_KEY] if credentials else None,
        )

    def _entry_being_updated(self) -> AnodeConfigEntry | None:
        if self.source == SOURCE_REAUTH:
            return self._get_reauth_entry()
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return None

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
        """Choose how to connect."""
        return self.async_show_menu(step_id="user", menu_options=CONNECTION_MENU)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the credentials were rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to reconnect."""
        return self.async_show_menu(
            step_id="reauth_confirm",
            menu_options=CONNECTION_MENU,
            description_placeholders={"hub_id": self._get_reauth_entry().data[CONF_HUB_ID]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to connect instead."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=CONNECTION_MENU,
            description_placeholders={"hub_id": self._get_reconfigure_entry().data[CONF_HUB_ID]},
        )

    # API key -------------------------------------------------------------------

    async def async_step_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect with an email and API key."""
        if (entry := self._entry_being_updated()) is not None:
            return await self._async_step_update_api_key(entry, user_input)

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
                        data={CONF_AUTH_TYPE: AUTH_API_KEY, **credentials, CONF_HUB_ID: hub_id},
                    )
                errors["base"] = error

        return self.async_show_form(
            step_id="api_key",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
        )

    async def _async_step_update_api_key(
        self, entry: AnodeConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        hub_id = entry.data[CONF_HUB_ID]
        if user_input is not None:
            credentials = _credentials(user_input)
            if (error := await self._async_validate_hub(credentials, hub_id)) is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data={CONF_AUTH_TYPE: AUTH_API_KEY, **credentials, CONF_HUB_ID: hub_id},
                )
            errors["base"] = error

        suggested = user_input
        if suggested is None and entry.data.get(CONF_AUTH_TYPE, AUTH_API_KEY) == AUTH_API_KEY:
            suggested = {CONF_EMAIL: entry.data[CONF_EMAIL]}
        return self.async_show_form(
            step_id="api_key",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, suggested),
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
                    data={CONF_AUTH_TYPE: AUTH_API_KEY, **self._credentials, CONF_HUB_ID: hub_id},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="hub",
            data_schema=self.add_suggested_values_to_schema(HUB_SCHEMA, user_input),
            errors=errors,
        )

    # Linking -------------------------------------------------------------------

    def _link_client_name(self) -> str:
        location = self.hass.config.location_name
        return f"Home Assistant ({location})" if location else "Home Assistant"

    async def _async_start_link(self) -> ConfigFlowResult | None:
        """Ask for a code and start polling, or return the abort to show.

        Polling runs from the moment the code is issued, whether or not the
        waiting screen is on show yet, so a code approved while the QR is still
        up is already collected by the time the flow moves on.
        """
        if self._link_task is not None:
            return None
        try:
            self._link_code = await self._client().start_link(self._link_client_name())
        except AnodeRateLimitError:
            return self.async_abort(reason="link_rate_limited")
        except AnodeError as err:
            _LOGGER.debug("Could not start linking: %s", err)
            return self.async_abort(reason="cannot_connect")
        # A fresh code has not been shown yet, so a second run through the QR
        # step shows it rather than going straight back to waiting.
        self._qr_shown = False
        self._link_task = self.hass.async_create_task(
            self._async_wait_for_link(self._link_code)
        )
        return None

    async def async_step_link_qr(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a code to scan, for approving on a phone, then wait here."""
        if (abort := await self._async_start_link()) is not None:
            return abort
        assert self._link_code is not None

        # Home Assistant re-enters whichever step put the waiting screen up, so
        # once the code has been seen this step has to be that screen too.
        self._qr_shown = self._qr_shown or user_input is not None
        if self._qr_shown:
            return await self._async_link_progress()

        return self.async_show_form(
            step_id="link_qr",
            data_schema=vol.Schema(
                {
                    vol.Optional("qr_code"): QrCodeSelector(
                        config=QrCodeSelectorConfig(
                            data=self._link_code.verification_url_complete,
                            scale=QR_SCALE,
                            error_correction_level=QrErrorCorrectionLevel.QUARTILE,
                        )
                    )
                }
            ),
            description_placeholders={
                "url": self._link_code.verification_url_complete,
                "code": self._link_code.user_code,
            },
        )

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the link and wait for the account holder to approve it."""
        if (abort := await self._async_start_link()) is not None:
            return abort
        return await self._async_link_progress()

    async def _async_link_progress(self) -> ConfigFlowResult:
        """Wait on the polling task, from whichever step is showing the wait."""
        assert self._link_code is not None and self._link_task is not None

        if not self._link_task.done():
            return self.async_show_progress(
                progress_action="wait_for_link",
                description_placeholders={
                    "url": self._link_code.verification_url_complete,
                    "code": self._link_code.user_code,
                },
                progress_task=self._link_task,
            )

        task, self._link_task = self._link_task, None
        try:
            self._grant = task.result()
        except AnodeLinkDeniedError:
            return self.async_show_progress_done(next_step_id="link_denied")
        except AnodeLinkExpiredError:
            return self.async_show_progress_done(next_step_id="link_expired")
        except Exception:
            _LOGGER.exception("Linking failed")
            return self.async_show_progress_done(next_step_id="link_failed")
        return self.async_show_progress_done(next_step_id="link_finish")

    async def _async_wait_for_link(self, code: LinkCode) -> LinkGrant:
        """Poll until the link is approved, refused or runs out."""
        client = self._client()
        deadline = time.monotonic() + code.expires_in
        interval = max(code.interval, LINK_MIN_POLL_INTERVAL)
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                grant = await client.poll_link(code.device_code)
            except (AnodeConnectionError, AnodeResponseError) as err:
                # Includes rate limiting; the code is still valid, keep trying.
                _LOGGER.debug("Link poll failed, retrying: %s", err)
                continue
            if grant is not None:
                return grant
        raise AnodeLinkExpiredError("The link code expired")

    async def async_step_link_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create or update the entry with the issued key."""
        assert self._grant is not None
        grant = self._grant

        # The grant is all or nothing, so a key that cannot even read is one the
        # account was not allowed to give away rather than one to store.
        if SCOPE_DEVICE_READ not in grant.scopes:
            return self.async_abort(reason="link_insufficient")

        # Asks the key what it reaches, which names the hub for an account-bound
        # grant and gives the entry its title for either.
        linked = AnodeClient(async_get_clientsession(self.hass), None, grant.api_key)
        hub: AccountHub | None = None
        try:
            hub = await linked.get_account_hub()
        except AnodeError as err:
            _LOGGER.debug("Could not read the linked hub: %s", err)

        hub_id = (grant.hub_id or (hub.hub_id if hub else None) or "").lower()
        if not hub_id:
            return self.async_abort(reason="cannot_connect")

        data = {
            CONF_AUTH_TYPE: AUTH_LINK,
            CONF_EMAIL: grant.email,
            CONF_API_KEY: grant.api_key,
            CONF_HUB_ID: hub_id,
            CONF_READ_ONLY: grant.read_only,
            CONF_FIRMWARE: grant.can_update_firmware,
        }
        await self.async_set_unique_id(hub_id)

        if (entry := self._entry_being_updated()) is not None:
            self._abort_if_unique_id_mismatch(reason="wrong_hub")
            return self.async_update_reload_and_abort(entry, data=data)

        self._abort_if_unique_id_configured()
        title = f"Anode Hub {hub_id}"
        if hub is not None and hub.hub_id.lower() == hub_id and hub.alias:
            title = hub.alias
        return self.async_create_entry(title=title, data=data)

    async def async_step_link_expired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a new code, or an API key, when the last code ran out."""
        # A menu rather than a form: choosing "link" runs the link step as
        # itself, which its progress screen needs.
        return self.async_show_menu(step_id="link_expired", menu_options=CONNECTION_MENU)

    async def async_step_link_denied(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The account holder refused the link."""
        return self.async_abort(reason="link_denied")

    async def async_step_link_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Linking failed after it was approved, so the key is already spent."""
        return self.async_abort(reason="link_failed")

    @callback
    def async_remove(self) -> None:
        """Stop polling if the flow is closed."""
        if self._link_task is not None and not self._link_task.done():
            self._link_task.cancel()

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

"""Tests for the override mode select, override duration and cancel button."""
from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.anode_battery.const import DOMAIN

from .common import HUB_ID, MODE, OVERRIDE, AnodeCloud, entity_id, state

SELECT = f"{HUB_ID}_override_mode"
DURATION = f"{HUB_ID}_override_duration"
CANCEL = f"{HUB_ID}_cancel_override"
OVERRIDE_ACTIVE = f"{HUB_ID}_override_active"


def overrides_sent(cloud: AnodeCloud) -> list[dict[str, str]]:
    return [dict(url.query) for url, _ in cloud.calls("PUT", OVERRIDE)]


async def select_mode(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id(hass, "select", SELECT), ATTR_OPTION: option},
        blocking=True,
    )


@pytest.mark.usefixtures("frozen_time")
async def test_override_mode_shows_running_mode(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The select reflects what the hub is doing now."""
    current = state(hass, "select", SELECT)
    assert current.state == "charge"
    assert current.attributes["options"] == ["charge", "discharge", "idle", "match"]
    assert float(state(hass, "number", DURATION).state) == 60


@pytest.mark.usefixtures("frozen_time")
async def test_select_overrides_for_duration(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Choosing a mode overrides the schedule for the configured duration."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id(hass, "number", DURATION), ATTR_VALUE: 30},
        blocking=True,
    )
    await select_mode(hass, "discharge")

    assert overrides_sent(cloud) == [{"mode": "DISCHARGE", "timeout": "1800"}]
    assert state(hass, "select", SELECT).state == "discharge"
    assert state(hass, "binary_sensor", OVERRIDE_ACTIVE).state == STATE_ON


async def test_override_confirmed_with_hub(
    hass: HomeAssistant,
    frozen_time,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
) -> None:
    """Shortly after a command the mode is re-read, and the hub has the last word."""
    cloud.respond("GET", MODE, json={"mode": "IDLE"})
    await select_mode(hass, "discharge")
    assert state(hass, "select", SELECT).state == "discharge"

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, "select", SELECT).state == "idle"


@pytest.mark.usefixtures("frozen_time")
async def test_cancel_returns_to_schedule(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Cancel sends MATCH for zero seconds and shows the scheduled mode."""
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: entity_id(hass, "button", CANCEL)},
        blocking=True,
    )
    assert overrides_sent(cloud) == [{"mode": "MATCH", "timeout": "0"}]
    assert state(hass, "select", SELECT).state == "match"
    assert state(hass, "binary_sensor", OVERRIDE_ACTIVE).state == STATE_OFF


async def test_override_rejected(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A rejected override raises and leaves the displayed mode alone."""
    cloud.respond(
        "PUT", OVERRIDE, json={"mode": '{"status":false,"info":"Failed to propagate event"}'}
    )
    with pytest.raises(HomeAssistantError) as err:
        await select_mode(hass, "idle")
    assert err.value.translation_key == "command_failed"
    assert state(hass, "select", SELECT).state == "charge"


async def test_command_auth_failure_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A rejected key during a command asks the user to re-authenticate."""
    cloud.respond("PUT", OVERRIDE, status=HTTPStatus.UNAUTHORIZED)
    with pytest.raises(HomeAssistantError) as err:
        await select_mode(hass, "idle")
    await hass.async_block_till_done()
    assert err.value.translation_key == "auth_failed"
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_override_duration_restored(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """The duration survives a restart and is used by the select."""
    mock_config_entry.add_to_hass(hass)
    number_id = er.async_get(hass).async_get_or_create(
        "number",
        DOMAIN,
        DURATION,
        suggested_object_id="home_hub_override_duration",
        config_entry=mock_config_entry,
    ).entity_id
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(number_id, "45"),
                {
                    "native_value": 45,
                    "native_unit_of_measurement": "min",
                    "native_min_value": 1,
                    "native_max_value": 10080,
                    "native_step": 1,
                },
            )
        ],
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(number_id).state) == 45
    await select_mode(hass, "charge")
    assert overrides_sent(cloud) == [{"mode": "CHARGE", "timeout": "2700"}]

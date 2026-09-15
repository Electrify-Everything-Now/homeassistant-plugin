"""Tests for Anode number entities."""
from __future__ import annotations

from http import HTTPStatus

import pytest

from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.const import DOMAIN

from .common import (
    HUB_ID,
    MAX_CHARGE,
    SET_CONFIG,
    SOC_CONFIG,
    AnodeCloud,
    entity_id,
    load_fixture,
    state,
)


async def set_value(hass: HomeAssistant, unique_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id(hass, "number", unique_id), ATTR_VALUE: value},
        blocking=True,
    )


async def test_soc_limits_loaded(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """SOC limits show the hub's values."""
    assert float(state(hass, "number", "bat01_min_soc").state) == 10
    assert float(state(hass, "number", "bat01_max_soc").state) == 90
    assert float(state(hass, "number", "bat02_max_soc").state) == 100


async def test_set_soc_limit_uses_fresh_counterpart(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Setting one end re-reads the other so app changes are not overwritten."""
    soc = load_fixture("config_soc.json")
    soc["value"][0]["config"]["maxSoc"] = 80  # changed in the Anode app
    cloud.respond("GET", SOC_CONFIG, json=soc)

    await set_value(hass, "bat01_min_soc", 25)
    assert cloud.calls("PUT", SET_CONFIG)[-1][1] == {
        "socConfig_bat01": {"minSoc": 25, "maxSoc": 80}
    }


async def test_soc_window_validated(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A minimum above the maximum is refused before anything is sent."""
    with pytest.raises(ServiceValidationError) as err:
        await set_value(hass, "bat01_min_soc", 95)
    assert err.value.translation_key == "invalid_soc_window"
    assert cloud.calls("PUT", SET_CONFIG) == []


async def test_power_limits(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Percent firmware exposes watts and percent controls."""
    assert float(state(hass, "number", f"{HUB_ID}_max_charge_power").state) == 4400
    assert float(state(hass, "number", f"{HUB_ID}_max_charge_power_percent").state) == 100
    assert float(state(hass, "number", f"{HUB_ID}_max_discharge_power_percent").state) == 80

    await set_value(hass, f"{HUB_ID}_max_charge_power", 3000)
    await set_value(hass, f"{HUB_ID}_max_discharge_power_percent", 50)
    assert [body for _, body in cloud.calls("PUT", SET_CONFIG)] == [
        {"maxChargePower": {"watts": 3000}},
        {"maxDischargePower": {"percent": 50}},
    ]


async def test_legacy_power_limit_firmware(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Firmware storing plain watts gets no percent control and bare writes."""
    cloud.respond("GET", MAX_CHARGE, json={"status": True, "key": "maxChargePower", "value": 2000})
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("number", DOMAIN, f"{HUB_ID}_max_charge_power_percent") is None
    assert float(state(hass, "number", f"{HUB_ID}_max_charge_power").state) == 2000

    await set_value(hass, f"{HUB_ID}_max_charge_power", 2500)
    assert cloud.calls("PUT", SET_CONFIG)[-1][1] == {"maxChargePower": 2500}


async def test_settings_unavailable(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """Setup continues when settings cannot be read; the controls wait."""
    for route in (SOC_CONFIG, MAX_CHARGE, f"/api/device/config/{HUB_ID}/maxDischargePower"):
        cloud.respond("GET", route, status=HTTPStatus.REQUEST_TIMEOUT)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, "number", "bat01_min_soc").state == STATE_UNAVAILABLE
    assert state(hass, "number", f"{HUB_ID}_max_charge_power").state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("response", "translation_key"),
    [
        ({"json": {"status": False, "info": "busy"}}, "command_failed"),
        ({"status": HTTPStatus.REQUEST_TIMEOUT}, "hub_offline_command"),
    ],
)
async def test_write_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    cloud: AnodeCloud,
    response: dict,
    translation_key: str,
) -> None:
    """Rejected or unanswered writes raise a translated error."""
    cloud.respond("PUT", SET_CONFIG, **response)
    with pytest.raises(HomeAssistantError) as err:
        await set_value(hass, f"{HUB_ID}_max_charge_power", 1000)
    assert err.value.translation_key == translation_key

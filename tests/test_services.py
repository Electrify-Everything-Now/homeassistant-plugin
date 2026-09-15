"""Tests for Anode actions."""
from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.const import DOMAIN

from .common import HUB_ID, OVERRIDE, AnodeCloud, state


def device_id(hass: HomeAssistant, identifier: str) -> str:
    device = dr.async_get(hass).async_get_device({(DOMAIN, identifier)})
    assert device is not None
    return device.id


def overrides_sent(cloud: AnodeCloud) -> list[dict[str, str]]:
    return [dict(url.query) for url, _ in cloud.calls("PUT", OVERRIDE)]


async def call(hass: HomeAssistant, service: str, data: dict[str, Any]) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


@pytest.mark.parametrize("target", [HUB_ID, "bat01", "grid1"])
async def test_set_override_by_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud, target: str
) -> None:
    """The hub or any of its devices can be targeted."""
    await call(
        hass,
        "set_override",
        {"device_id": device_id(hass, target), "mode": "IDLE", "duration": 900},
    )
    assert overrides_sent(cloud) == [{"mode": "IDLE", "timeout": "900"}]
    assert state(hass, "sensor", f"{HUB_ID}_mode").state == "IDLE"


async def test_set_override_by_legacy_hub_id(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Automations from 0.1 using hub_id and any mode casing keep working."""
    await call(hass, "set_override", {"hub_id": HUB_ID, "mode": "discharge", "duration": 3600})
    assert overrides_sent(cloud) == [{"mode": "DISCHARGE", "timeout": "3600"}]


@pytest.mark.usefixtures("frozen_time")
async def test_zero_duration_and_cancel(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A zero duration and the cancel action both return to the schedule."""
    await call(hass, "set_override", {"hub_id": HUB_ID, "mode": "CHARGE", "duration": 0})
    assert state(hass, "sensor", f"{HUB_ID}_mode").state == "MATCH"

    await call(hass, "cancel_override", {"device_id": device_id(hass, HUB_ID)})
    assert overrides_sent(cloud) == [
        {"mode": "CHARGE", "timeout": "0"},
        {"mode": "MATCH", "timeout": "0"},
    ]


@pytest.mark.parametrize(
    ("data", "translation_key"),
    [
        ({"device_id": "not-a-device"}, "device_not_found"),
        ({"hub_id": "other"}, "hub_not_found"),
    ],
)
async def test_unknown_targets(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    data: dict[str, str],
    translation_key: str,
) -> None:
    """Unknown targets raise a translated validation error."""
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "cancel_override", data)
    assert err.value.translation_key == translation_key


async def test_device_from_another_integration(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A device that is not part of an Anode hub is refused."""
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", "thing")}
    )
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "cancel_override", {"device_id": device.id})
    assert err.value.translation_key == "hub_not_found"


@pytest.mark.parametrize("data", [{}, {"device_id": "abc", "hub_id": HUB_ID}])
async def test_exactly_one_target(
    hass: HomeAssistant, init_integration: MockConfigEntry, data: dict[str, str]
) -> None:
    """Either device_id or hub_id must be given, not both."""
    with pytest.raises(vol.Invalid):
        await call(hass, "cancel_override", data)


async def test_hub_not_loaded(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Actions on an unloaded hub explain why they cannot run."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "cancel_override", {"hub_id": HUB_ID})
    assert err.value.translation_key == "hub_not_loaded"

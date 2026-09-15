"""Tests for Anode binary sensors."""
from __future__ import annotations

from dataclasses import replace
from datetime import time
from http import HTTPStatus

import pytest

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.api import OperatingMode, ScheduleSlot
from custom_components.anode_battery.coordinator import ModeState

from .common import HUB_ID, MODE, STATUS, AnodeCloud, load_fixture, refresh, state


async def test_online(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Hub and devices read as connected."""
    assert state(hass, "binary_sensor", f"{HUB_ID}_online").state == STATE_ON
    assert state(hass, "binary_sensor", "bat01_online").state == STATE_ON
    assert state(hass, "binary_sensor", "grid1_online").state == STATE_ON


async def test_hub_unreachable(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """An unreachable hub reads as disconnected; its devices as unknown."""
    cloud.respond("GET", STATUS, status=HTTPStatus.REQUEST_TIMEOUT)
    await refresh(hass, init_integration.runtime_data.status)
    assert state(hass, "binary_sensor", f"{HUB_ID}_online").state == STATE_OFF
    assert state(hass, "binary_sensor", "bat01_online").state == STATE_UNAVAILABLE

    cloud.respond("GET", STATUS, json=load_fixture("status.json"))
    await refresh(hass, init_integration.runtime_data.status)
    assert state(hass, "binary_sensor", f"{HUB_ID}_online").state == STATE_ON


async def test_device_offline_flag(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """The hub's per-device online flag is honoured."""
    status = load_fixture("status.json")
    status["battery"][1]["online"] = False
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)
    assert state(hass, "binary_sensor", "bat02_online").state == STATE_OFF


@pytest.mark.usefixtures("frozen_time")
async def test_override_active(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Charging at noon, when the schedule has no slot, is an override."""
    assert state(hass, "binary_sensor", f"{HUB_ID}_override_active").state == STATE_ON

    cloud.respond("GET", MODE, json={"mode": "MATCH"})
    await refresh(hass, init_integration.runtime_data.mode)
    assert state(hass, "binary_sensor", f"{HUB_ID}_override_active").state == STATE_OFF


def test_override_active_rules() -> None:
    """Idling inside a slot with a target SOC follows the schedule."""
    slot = ScheduleSlot(time(1), time(6), OperatingMode.CHARGE, target_soc=80)
    base = ModeState(
        mode=OperatingMode.IDLE,
        schedule=(slot,),
        scheduled_mode=OperatingMode.CHARGE,
        active_slot=slot,
        next_mode=None,
        next_change=None,
    )
    assert base.override_active is False
    assert replace(base, active_slot=None).override_active is True
    assert replace(base, mode=OperatingMode.CHARGE).override_active is False
    assert replace(base, mode=OperatingMode.DISCHARGE).override_active is True
    assert replace(base, mode=None).override_active is None

"""Tests for Anode diagnostics."""
from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.usefixtures("frozen_time")
async def test_diagnostics(
    hass: HomeAssistant, init_integration: MockConfigEntry, snapshot: SnapshotAssertion
) -> None:
    """Diagnostics include every coordinator's data with credentials removed."""
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    assert diagnostics["entry"]["data"]["email"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
    assert diagnostics == snapshot

"""Test Anode Battery coordinators."""
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.anode_battery.coordinator import (
    AnodeStatusCoordinator,
    AnodeDeviceCoordinator,
    AnodeModeCoordinator,
)


async def test_status_coordinator_success(hass: HomeAssistant, mock_anode_api) -> None:
    """Test status coordinator successfully fetches data."""
    coordinator = AnodeStatusCoordinator(hass, mock_anode_api, 120)

    await coordinator.async_config_entry_first_refresh()

    assert coordinator.data is not None
    assert coordinator.data["status"] is True
    assert "hub" in coordinator.data
    assert "battery" in coordinator.data
    assert "meter" in coordinator.data
    mock_anode_api.get_hub_status.assert_called_once()


async def test_device_coordinator_success(hass: HomeAssistant, mock_anode_api) -> None:
    """Test device coordinator successfully fetches data."""
    coordinator = AnodeDeviceCoordinator(hass, mock_anode_api, 10)
    coordinator.set_device_ids(["battery1"], ["meter1"])

    await coordinator.async_config_entry_first_refresh()

    assert coordinator.data is not None
    assert "batteries" in coordinator.data
    assert "meters" in coordinator.data
    assert "battery1" in coordinator.data["batteries"]
    assert "meter1" in coordinator.data["meters"]
    assert mock_anode_api.get_battery_details.call_count == 1
    assert mock_anode_api.get_meter_details.call_count == 1


async def test_mode_coordinator_success(hass: HomeAssistant, mock_anode_api) -> None:
    """Test mode coordinator successfully fetches data."""
    coordinator = AnodeModeCoordinator(hass, mock_anode_api)

    await coordinator.async_config_entry_first_refresh()

    assert coordinator.data is not None
    assert coordinator.data["mode"] == "CHARGE"
    assert "schedule" in coordinator.data
    assert "next_mode" in coordinator.data
    assert "next_time" in coordinator.data
    mock_anode_api.get_mode.assert_called_once()
    mock_anode_api.get_schedule.assert_called_once()


async def test_coordinator_handles_api_error(hass: HomeAssistant, mock_anode_api) -> None:
    """Test coordinator handles API errors."""
    mock_anode_api.get_hub_status.side_effect = Exception("API Error")

    coordinator = AnodeStatusCoordinator(hass, mock_anode_api, 120)

    with pytest.raises(UpdateFailed):
        await coordinator.async_config_entry_first_refresh()


async def test_mode_coordinator_calculates_next_schedule(hass: HomeAssistant, mock_anode_api) -> None:
    """Test mode coordinator calculates next schedule time."""
    coordinator = AnodeModeCoordinator(hass, mock_anode_api)

    await coordinator.async_config_entry_first_refresh()

    # Should have calculated next mode and time
    assert coordinator.data.get("next_mode") is not None or coordinator.data.get("next_time") is not None

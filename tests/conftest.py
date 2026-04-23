"""Fixtures for Anode integration tests."""
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.const import DOMAIN, CONF_API_KEY, CONF_HUB_ID
from homeassistant.const import CONF_EMAIL


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    return enable_custom_integrations


@pytest.fixture
def mock_anode_api():
    """Mock Anode API client."""
    with patch(
        "custom_components.anode_battery.AnodeAPIClient",
        autospec=True,
    ) as mock_api:
        api_instance = mock_api.return_value
        api_instance.hub_id = "test123"
        api_instance.get_hub_status = AsyncMock(return_value={
            "status": True,
            "hub": {
                "version": "1.2.3",
                "uptime": 86400000,
            },
            "battery": [
                {
                    "id": "battery1",
                    "version": "1.0.0",
                    "uptime": 43200000,
                    "type": None,
                }
            ],
            "meter": [
                {
                    "id": "meter1",
                    "version": "1.0.0",
                    "uptime": 43200000,
                    "type": "PRIMARY",
                },
                {
                    "id": "meter2",
                    "version": "1.0.0",
                    "uptime": 43200000,
                    "type": "EXT_INVERTER",
                }
            ],
        })
        api_instance.get_battery_details = AsyncMock(return_value={
            "power": {"value": 1500.0, "unit": "W"},
            "powerStatus": "CHARGING",
            "soc": {"value": 75.0, "unit": "%"},
            "capacity": {"value": 136, "nominalVoltage": 44.4, "calibrated": True},
            "importEnergy": {"value": 9226910, "unit": "dWh"},
            "exportEnergy": {"value": 7304280, "unit": "dWh"},
        })
        api_instance.get_meter_details = AsyncMock(return_value={
            "power": {"value": 2000.0, "unit": "W"},
        })
        api_instance.get_mode = AsyncMock(return_value="CHARGE")
        api_instance.get_schedule = AsyncMock(return_value={
            "status": True,
            "schedule": [
                {
                    "begin": {"hour": 1, "minute": 0, "second": 0},
                    "end": {"hour": 6, "minute": 0, "second": 0},
                    "mode": "CHARGE",
                }
            ],
        })
        api_instance.set_override = AsyncMock(return_value={
            "mode": "CHARGE"
        })
        api_instance.get_telemetry = AsyncMock(return_value={
            "import": 500.0,
            "export": 200.0,
        })
        api_instance.get_config = AsyncMock(return_value={"config": []})
        api_instance.set_config = AsyncMock(return_value={"status": True})
        api_instance.get_device_metadata = AsyncMock(return_value=[
            {"friendlyId": "meter1", "alias": "Grid", "meterPurpose": "primary"},
            {"friendlyId": "meter2", "alias": "Solar", "meterPurpose": "solar"},
        ])
        yield api_instance


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Mock a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Anode Hub test123",
        data={
            CONF_EMAIL: "test@example.com",
            CONF_API_KEY: "test_api_key",
            CONF_HUB_ID: "test123",
        },
        unique_id="test123",
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_anode_api,
) -> MockConfigEntry:
    """Set up the Anode integration for testing."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry

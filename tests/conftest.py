"""Fixtures for Anode integration tests."""
from __future__ import annotations

from datetime import UTC, datetime

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.anode_battery.const import CONF_HUB_ID, DOMAIN

from .common import API_KEY, EMAIL, HUB_ID, AnodeCloud


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in every test."""


@pytest.fixture
def cloud(aioclient_mock: AiohttpClientMocker) -> AnodeCloud:
    """A fake Anode cloud answering with fixture data."""
    return AnodeCloud(aioclient_mock)


@pytest.fixture
async def frozen_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> datetime:
    """Noon in London on a day with the fixture schedule's slots ahead."""
    await hass.config.async_set_time_zone("Europe/London")
    now = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)
    freezer.move_to(now)
    return now


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry for the fixture hub."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home hub",
        unique_id=HUB_ID,
        version=1,
        minor_version=2,
        data={CONF_EMAIL: EMAIL, CONF_API_KEY: API_KEY, CONF_HUB_ID: HUB_ID},
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, cloud: AnodeCloud
) -> MockConfigEntry:
    """Set up the integration against the fake cloud."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry

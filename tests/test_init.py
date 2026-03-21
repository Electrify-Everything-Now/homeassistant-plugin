"""Test Anode initialization."""
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState

from custom_components.anode_battery.const import DOMAIN


async def test_setup_entry(hass: HomeAssistant, init_integration) -> None:
    """Test successful setup."""
    assert init_integration.state == ConfigEntryState.LOADED
    assert DOMAIN in hass.data


async def test_unload_entry(hass: HomeAssistant, init_integration) -> None:
    """Test unload of an entry."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data


async def test_reload_entry(hass: HomeAssistant, init_integration) -> None:
    """Test reload of an entry."""
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.LOADED

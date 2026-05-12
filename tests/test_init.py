"""Test Anode initialization."""
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

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


async def test_aliases_applied_to_device_registry(
    hass: HomeAssistant, init_integration
) -> None:
    """Web-UI aliases land on every device (hub, battery, meter)."""
    device_registry = dr.async_get(hass)

    hub = device_registry.async_get_device(identifiers={(DOMAIN, "test123")})
    assert hub is not None and hub.name == "My Hub"

    battery = device_registry.async_get_device(identifiers={(DOMAIN, "battery1")})
    assert battery is not None and battery.name == "My Battery"

    meter1 = device_registry.async_get_device(identifiers={(DOMAIN, "meter1")})
    assert meter1 is not None and meter1.name == "Grid"

    meter2 = device_registry.async_get_device(identifiers={(DOMAIN, "meter2")})
    assert meter2 is not None and meter2.name == "Solar"


async def test_alias_updates_on_metadata_refresh(
    hass: HomeAssistant, init_integration, mock_anode_api
) -> None:
    """Renaming a device in the web UI is reflected after the next status refresh."""
    device_registry = dr.async_get(hass)
    coordinators = hass.data[DOMAIN][init_integration.entry_id]
    status_coordinator = coordinators["status_coordinator"]

    mock_anode_api.get_device_metadata.return_value = [
        {"friendlyId": "test123", "alias": "Renamed Hub", "meterPurpose": None},
        {"friendlyId": "battery1", "alias": "Renamed Battery", "meterPurpose": None},
        {"friendlyId": "meter1", "alias": "Renamed Grid", "meterPurpose": "primary"},
        {"friendlyId": "meter2", "alias": "Renamed Solar", "meterPurpose": "solar"},
    ]
    await status_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert device_registry.async_get_device(
        identifiers={(DOMAIN, "test123")}
    ).name == "Renamed Hub"
    assert device_registry.async_get_device(
        identifiers={(DOMAIN, "battery1")}
    ).name == "Renamed Battery"
    assert device_registry.async_get_device(
        identifiers={(DOMAIN, "meter1")}
    ).name == "Renamed Grid"


async def test_alias_preserves_user_rename(
    hass: HomeAssistant, init_integration
) -> None:
    """If the user renames a device in HA (name_by_user), that wins in the UI.

    Our sync updates `name`, not `name_by_user`, so HA's display logic keeps
    showing the user's preferred name.
    """
    device_registry = dr.async_get(hass)
    battery = device_registry.async_get_device(identifiers={(DOMAIN, "battery1")})
    device_registry.async_update_device(battery.id, name_by_user="My Custom Name")

    coordinators = hass.data[DOMAIN][init_integration.entry_id]
    await coordinators["status_coordinator"].async_refresh()
    await hass.async_block_till_done()

    battery = device_registry.async_get_device(identifiers={(DOMAIN, "battery1")})
    assert battery.name == "My Battery"
    assert battery.name_by_user == "My Custom Name"

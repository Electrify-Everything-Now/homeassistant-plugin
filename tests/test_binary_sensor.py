"""Test Anode Battery binary sensors."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


async def test_binary_sensors_created(hass: HomeAssistant, init_integration) -> None:
    """Test that binary sensors are created."""
    entity_registry = er.async_get(hass)

    # Hub online sensor
    assert entity_registry.async_get("binary_sensor.anode_hub_test123_online") is not None

    # Hub override sensor
    assert entity_registry.async_get("binary_sensor.anode_hub_test123_override_active") is not None

    # Battery online sensor
    assert entity_registry.async_get("binary_sensor.anode_battery_battery1_online") is not None

    # Meter online sensors
    assert entity_registry.async_get("binary_sensor.anode_meter_meter1_online") is not None
    assert entity_registry.async_get("binary_sensor.anode_meter_meter2_online") is not None


async def test_hub_online_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test hub online sensor."""
    state = hass.states.get("binary_sensor.anode_hub_test123_online")
    assert state is not None
    assert state.state == "on"  # status is True in mock


async def test_battery_online_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test battery online sensor."""
    state = hass.states.get("binary_sensor.anode_battery_battery1_online")
    assert state is not None
    assert state.state == "on"  # Battery in status list


async def test_meter_online_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test meter online sensor."""
    state = hass.states.get("binary_sensor.anode_meter_meter1_online")
    assert state is not None
    assert state.state == "on"  # Meter in status list

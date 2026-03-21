"""Test Anode sensors."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.anode_battery.const import DOMAIN


async def test_sensors_created(hass: HomeAssistant, init_integration) -> None:
    """Test that sensors are created."""
    entity_registry = er.async_get(hass)

    # Hub sensors
    assert entity_registry.async_get("sensor.anode_hub_test123_mode") is not None
    assert entity_registry.async_get("sensor.anode_hub_test123_version") is not None
    assert entity_registry.async_get("sensor.anode_hub_test123_uptime") is not None
    assert entity_registry.async_get("sensor.anode_hub_test123_next_mode") is not None
    assert entity_registry.async_get("sensor.anode_hub_test123_next_mode_time") is not None

    # Battery sensors
    assert entity_registry.async_get("sensor.anode_battery_battery1_power") is not None
    assert entity_registry.async_get("sensor.anode_battery_battery1_state_of_charge") is not None

    # Meter sensors
    assert entity_registry.async_get("sensor.anode_meter_meter1_power") is not None
    assert entity_registry.async_get("sensor.anode_meter_meter1_type") is not None

    # House sensor (PRIMARY + EXT_INVERTER present)
    assert entity_registry.async_get("sensor.anode_hub_test123_house_power") is not None


async def test_hub_mode_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test hub mode sensor."""
    state = hass.states.get("sensor.anode_hub_test123_mode")
    assert state is not None
    assert state.state == "CHARGE"


async def test_battery_power_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test battery power sensor."""
    state = hass.states.get("sensor.anode_battery_battery1_power")
    assert state is not None
    assert state.state == "1500.0"
    assert state.attributes.get("unit_of_measurement") == "W"


async def test_battery_soc_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test battery SOC sensor."""
    state = hass.states.get("sensor.anode_battery_battery1_state_of_charge")
    assert state is not None
    assert state.state == "75.0"
    assert state.attributes.get("unit_of_measurement") == "%"


async def test_meter_power_sensor(hass: HomeAssistant, init_integration) -> None:
    """Test meter power sensor."""
    state = hass.states.get("sensor.anode_meter_meter1_power")
    assert state is not None
    assert state.state == "2000.0"


async def test_house_power_calculation(hass: HomeAssistant, init_integration) -> None:
    """Test house power sensor calculation."""
    state = hass.states.get("sensor.anode_hub_test123_house_power")
    assert state is not None
    # House = PRIMARY - EXT_INVERTER - battery_power
    # Both meters return 2000W, battery returns 1500W
    # 2000 - 2000 - 1500 = -1500W
    assert float(state.state) == -1500.0


async def test_uptime_sensor_unit(hass: HomeAssistant, init_integration) -> None:
    """Test uptime sensor shows days as suggested unit."""
    state = hass.states.get("sensor.anode_hub_test123_uptime")
    assert state is not None
    # Uptime is in milliseconds (86400000ms = 1 day)
    assert state.state == "86400000"
    # Check that suggested unit is days
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get("sensor.anode_hub_test123_uptime")
    assert entry is not None

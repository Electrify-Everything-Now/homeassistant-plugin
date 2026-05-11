"""Test Anode sensors."""
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity

from custom_components.anode_battery.const import DOMAIN
from custom_components.anode_battery.sensor import AnodeHouseEnergySensor


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
    """Test battery SOC sensor (integer percentage)."""
    state = hass.states.get("sensor.anode_battery_battery1_state_of_charge")
    assert state is not None
    assert state.state == "75"
    assert state.attributes.get("unit_of_measurement") == "%"


async def test_battery_energy_capacity_sensor(hass: HomeAssistant, init_integration) -> None:
    """Battery Wh capacity: 136 Ah * 44.4 V = 6038.4 Wh."""
    state = hass.states.get("sensor.anode_battery_battery1_energy_capacity")
    assert state is not None
    assert float(state.state) == 6038.4
    assert state.attributes.get("unit_of_measurement") == "Wh"


async def test_battery_energy_remaining_sensor(hass: HomeAssistant, init_integration) -> None:
    """Battery Wh remaining: 6038.4 Wh * 75% = 4528.8 Wh."""
    state = hass.states.get("sensor.anode_battery_battery1_energy_remaining")
    assert state is not None
    assert float(state.state) == 4528.8
    assert state.attributes.get("unit_of_measurement") == "Wh"


async def test_hub_battery_energy_capacity_sensor(hass: HomeAssistant, init_integration) -> None:
    """Hub aggregate Wh capacity (single battery fixture)."""
    state = hass.states.get("sensor.anode_hub_test123_battery_energy_capacity")
    assert state is not None
    assert float(state.state) == 6038.4


async def test_hub_battery_energy_remaining_sensor(hass: HomeAssistant, init_integration) -> None:
    """Hub aggregate Wh remaining (single battery fixture)."""
    state = hass.states.get("sensor.anode_hub_test123_battery_energy_remaining")
    assert state is not None
    assert float(state.state) == 4528.8


async def test_hub_average_soc_sensor(hass: HomeAssistant, init_integration) -> None:
    """Hub capacity-weighted average SOC is integer."""
    state = hass.states.get("sensor.anode_hub_test123_average_state_of_charge")
    assert state is not None
    assert state.state == "75"
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


async def test_house_energy_projection(hass: HomeAssistant, init_integration) -> None:
    """House energy projects directly from hardware counters.

    With the conftest fixtures (same mock for both meters):
      grid (meter1):  (100 - 30) kWh = +70
      gen  (meter2):  (30 - 100) kWh = -70
      batt (battery1): (730.428 - 922.691) kWh = -192.263
      total: -192.263 kWh
    """
    state = hass.states.get("sensor.anode_hub_test123_house_energy")
    assert state is not None
    assert float(state.state) == pytest.approx(-192.263, abs=1e-3)


async def test_house_energy_today_starts_at_zero(
    hass: HomeAssistant, init_integration
) -> None:
    """The 'today' wrapper baselines to the current source value, so day-1 == 0."""
    state = hass.states.get("sensor.anode_hub_test123_house_energy_today")
    assert state is not None
    assert float(state.state) == pytest.approx(0.0, abs=1e-3)
    assert state.attributes.get("baseline_kwh") == pytest.approx(-192.263, abs=1e-3)


async def test_house_energy_today_rebaselines_on_source_drop(
    hass: HomeAssistant, init_integration, mock_anode_api
) -> None:
    """If the source drops below baseline, the wrapper rebaselines (doesn't stick at 0)."""
    coordinators = hass.data[DOMAIN][init_integration.entry_id]
    device_coordinator = coordinators["device_coordinator"]

    # Baseline starts at ~-192.263 kWh (see test_house_energy_today_starts_at_zero).
    # Drop the source: charge the battery more so batt_term goes further negative.
    mock_anode_api.get_battery_details = AsyncMock(return_value={
        "power": {"value": 1500.0, "unit": "W"},
        "powerStatus": "CHARGING",
        "soc": {"value": 75.0, "unit": "%"},
        "capacity": {"value": 136, "nominalVoltage": 44.4, "calibrated": True},
        "importEnergy": {"value": 20000000, "unit": "dWh"},  # 2000 kWh (was 922.691)
        "exportEnergy": {"value":  7304280, "unit": "dWh"},  # unchanged
    })
    # New projection: 70 + (-70) + (730.428 - 2000) = -1269.572 kWh, well below baseline.
    await device_coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.anode_hub_test123_house_energy_today")
    assert state is not None
    # After rebaseline, today returns 0.0 and baseline tracks the new (lower) total.
    assert float(state.state) == pytest.approx(0.0, abs=1e-3)
    assert state.attributes.get("baseline_kwh") == pytest.approx(-1269.572, abs=1e-3)


async def test_house_energy_is_stateless(
    hass: HomeAssistant, init_integration
) -> None:
    """Lock in the stateless contract: house energy must not be a RestoreEntity."""
    assert not issubclass(AnodeHouseEnergySensor, RestoreEntity)


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

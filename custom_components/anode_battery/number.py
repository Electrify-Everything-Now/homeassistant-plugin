"""Number platform for Anode integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_HUB_ID
from .coordinator import AnodeAPIClient, AnodeStatusCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode number entities."""
    hub_id = entry.data[CONF_HUB_ID]
    coordinators = hass.data[DOMAIN][entry.entry_id]

    status_coordinator: AnodeStatusCoordinator = coordinators["status_coordinator"]
    api_client: AnodeAPIClient = coordinators["api_client"]

    entities: list[NumberEntity] = []

    # SOC limits per battery
    for battery in status_coordinator.data.get("battery", []):
        battery_id = battery["id"]
        entities.extend([
            AnodeBatteryMinSOCNumber(api_client, hub_id, battery_id, entry.entry_id),
            AnodeBatteryMaxSOCNumber(api_client, hub_id, battery_id, entry.entry_id),
        ])

    # System power settings on hub
    entities.extend([
        AnodeHubMaxChargePowerNumber(api_client, hub_id, entry.entry_id),
        AnodeHubMaxDischargePowerNumber(api_client, hub_id, entry.entry_id),
    ])

    async_add_entities(entities)


class AnodeBatteryMinSOCNumber(NumberEntity):
    """Number entity for battery minimum state of charge."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-low"

    def __init__(
        self,
        api_client: AnodeAPIClient,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        self._api_client = api_client
        self._hub_id = hub_id
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_min_soc"
        self._attr_name = f"Anode {battery_id} Min SOC"
        self._attr_native_value: float | None = None
        self._cached_max_soc: float = 100.0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        await self._fetch_soc_config()

    async def _fetch_soc_config(self) -> None:
        try:
            result = await self._api_client.get_config("socConfig")
            configs = result if isinstance(result, list) else result.get("config", [])
            for item in configs:
                if item.get("id") == self._battery_id:
                    cfg = item.get("config", {})
                    self._attr_native_value = float(cfg.get("minSoc", 20))
                    self._cached_max_soc = float(cfg.get("maxSoc", 100))
                    self.async_write_ha_state()
                    return
        except Exception as err:
            _LOGGER.warning("Failed to fetch SOC config for %s: %s", self._battery_id, err)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._api_client.set_config({
                f"socConfig_{self._battery_id}": {
                    "minSoc": int(value),
                    "maxSoc": int(self._cached_max_soc),
                }
            })
            self._attr_native_value = value
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to set min SOC for %s: %s", self._battery_id, err)
            raise


class AnodeBatteryMaxSOCNumber(NumberEntity):
    """Number entity for battery maximum state of charge."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-high"

    def __init__(
        self,
        api_client: AnodeAPIClient,
        hub_id: str,
        battery_id: str,
        entry_id: str,
    ) -> None:
        self._api_client = api_client
        self._hub_id = hub_id
        self._battery_id = battery_id
        self._attr_unique_id = f"{battery_id}_max_soc"
        self._attr_name = f"Anode {battery_id} Max SOC"
        self._attr_native_value: float | None = None
        self._cached_min_soc: float = 20.0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
        )

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        await self._fetch_soc_config()

    async def _fetch_soc_config(self) -> None:
        try:
            result = await self._api_client.get_config("socConfig")
            configs = result if isinstance(result, list) else result.get("config", [])
            for item in configs:
                if item.get("id") == self._battery_id:
                    cfg = item.get("config", {})
                    self._cached_min_soc = float(cfg.get("minSoc", 20))
                    self._attr_native_value = float(cfg.get("maxSoc", 100))
                    self.async_write_ha_state()
                    return
        except Exception as err:
            _LOGGER.warning("Failed to fetch SOC config for %s: %s", self._battery_id, err)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._api_client.set_config({
                f"socConfig_{self._battery_id}": {
                    "minSoc": int(self._cached_min_soc),
                    "maxSoc": int(value),
                }
            })
            self._attr_native_value = value
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to set max SOC for %s: %s", self._battery_id, err)
            raise


class AnodeHubMaxChargePowerNumber(NumberEntity):
    """Number entity for hub maximum charge power."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100000
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-charging"

    def __init__(
        self,
        api_client: AnodeAPIClient,
        hub_id: str,
        entry_id: str,
    ) -> None:
        self._api_client = api_client
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_max_charge_power"
        self._attr_name = f"Anode Hub {hub_id} Max Charge Power"
        self._attr_native_value: float | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        try:
            result = await self._api_client.get_config("maxChargePower")
            value = result.get("value") if isinstance(result, dict) else None
            if value is not None:
                self._attr_native_value = float(value)
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.warning("Failed to fetch maxChargePower: %s", err)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._api_client.set_config({"maxChargePower": int(value)})
            self._attr_native_value = value
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to set maxChargePower: %s", err)
            raise


class AnodeHubMaxDischargePowerNumber(NumberEntity):
    """Number entity for hub maximum discharge power."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100000
    _attr_native_step = 100
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-minus"

    def __init__(
        self,
        api_client: AnodeAPIClient,
        hub_id: str,
        entry_id: str,
    ) -> None:
        self._api_client = api_client
        self._hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_max_discharge_power"
        self._attr_name = f"Anode Hub {hub_id} Max Discharge Power"
        self._attr_native_value: float | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
        )

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        try:
            result = await self._api_client.get_config("maxDischargePower")
            value = result.get("value") if isinstance(result, dict) else None
            if value is not None:
                self._attr_native_value = float(value)
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.warning("Failed to fetch maxDischargePower: %s", err)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._api_client.set_config({"maxDischargePower": int(value)})
            self._attr_native_value = value
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to set maxDischargePower: %s", err)
            raise

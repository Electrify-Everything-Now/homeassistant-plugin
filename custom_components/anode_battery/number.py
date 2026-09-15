"""Number platform for the Anode integration."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Literal

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PowerLimit, PowerLimitKey, SocLimits
from .const import DOMAIN, MAX_OVERRIDE_DURATION_MIN
from .coordinator import AnodeConfigEntry, AnodeRuntimeData, AnodeSettingsCoordinator
from .entity import AnodeEntity, async_run_command, async_setup_dynamic_entities, device_info

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AnodeSocLimitDescription(NumberEntityDescription):
    """One end of a battery's state-of-charge window."""

    bound: Literal["min", "max"]


SOC_LIMITS: tuple[AnodeSocLimitDescription, ...] = tuple(
    AnodeSocLimitDescription(
        key=f"{bound}_soc",
        translation_key=f"{bound}_soc",
        bound=bound,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )
    for bound in ("min", "max")
)


@dataclass(frozen=True, kw_only=True)
class AnodePowerLimitDescription(NumberEntityDescription):
    """A fleet charge or discharge power limit."""

    limit_key: PowerLimitKey
    as_percent: bool


POWER_LIMITS: tuple[AnodePowerLimitDescription, ...] = (
    *(
        AnodePowerLimitDescription(
            key=key,
            translation_key=key,
            limit_key=limit_key,
            as_percent=False,
            device_class=NumberDeviceClass.POWER,
            native_min_value=0,
            native_max_value=100_000,
            native_step=100,
            native_unit_of_measurement=UnitOfPower.WATT,
            mode=NumberMode.BOX,
            entity_category=EntityCategory.CONFIG,
        )
        for key, limit_key in (
            ("max_charge_power", PowerLimitKey.MAX_CHARGE),
            ("max_discharge_power", PowerLimitKey.MAX_DISCHARGE),
        )
    ),
    *(
        AnodePowerLimitDescription(
            key=key,
            translation_key=key,
            limit_key=limit_key,
            as_percent=True,
            native_min_value=0,
            native_max_value=100,
            native_step=1,
            native_unit_of_measurement=PERCENTAGE,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
        )
        for key, limit_key in (
            ("max_charge_power_percent", PowerLimitKey.MAX_CHARGE),
            ("max_discharge_power_percent", PowerLimitKey.MAX_DISCHARGE),
        )
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anode number entities."""
    runtime = entry.runtime_data

    def build() -> Iterator[Entity]:
        yield AnodeOverrideDurationNumber(runtime)
        settings = runtime.settings.data
        for description in POWER_LIMITS:
            limit = settings.power_limits.get(description.limit_key) if settings else None
            # Percent controls only exist on firmware that stores a percent.
            if not description.as_percent or (limit is not None and limit.is_percent_based):
                yield AnodePowerLimitNumber(runtime, description)
        for battery_id in runtime.status.data.batteries:
            for description in SOC_LIMITS:
                yield AnodeSocLimitNumber(runtime, battery_id, description)

    async_setup_dynamic_entities(entry, async_add_entities, build, runtime.settings)


class AnodeSocLimitNumber(AnodeEntity[AnodeSettingsCoordinator], NumberEntity):
    """Minimum or maximum state of charge for one battery."""

    entity_description: AnodeSocLimitDescription

    def __init__(
        self, runtime: AnodeRuntimeData, battery_id: str, description: AnodeSocLimitDescription
    ) -> None:
        super().__init__(runtime.settings, battery_id, description.key)
        self.entity_description = description
        self._runtime = runtime

    @property
    def _limits(self) -> SocLimits | None:
        data = self.coordinator.data
        return data.soc_limits.get(self._device_id) if data else None

    @property
    def available(self) -> bool:
        return super().available and self._limits is not None

    @property
    def native_value(self) -> float | None:
        if (limits := self._limits) is None:
            return None
        return limits.min_soc if self.entity_description.bound == "min" else limits.max_soc

    async def async_set_native_value(self, value: float) -> None:
        runtime = self._runtime

        async def apply() -> None:
            # Read the other end fresh so a change made in the Anode app since
            # our last poll is not overwritten.
            current = (await runtime.client.get_soc_limits(runtime.hub_id)).get(self._device_id)
            if current is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="soc_limits_unavailable",
                    translation_placeholders={"battery_id": self._device_id},
                )
            if self.entity_description.bound == "min":
                new = replace(current, min_soc=round(value))
            else:
                new = replace(current, max_soc=round(value))
            if new.min_soc > new.max_soc:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_soc_window",
                    translation_placeholders={
                        "min_soc": str(new.min_soc),
                        "max_soc": str(new.max_soc),
                    },
                )
            await runtime.client.set_soc_limits(runtime.hub_id, self._device_id, new)

        await async_run_command(self.hass, self.coordinator.config_entry, apply())
        await self.coordinator.async_request_refresh()


class AnodePowerLimitNumber(AnodeEntity[AnodeSettingsCoordinator], NumberEntity):
    """Fleet charge or discharge power limit, in watts or percent."""

    entity_description: AnodePowerLimitDescription

    def __init__(self, runtime: AnodeRuntimeData, description: AnodePowerLimitDescription) -> None:
        super().__init__(runtime.settings, runtime.hub_id, description.key)
        self.entity_description = description
        self._runtime = runtime

    @property
    def _limit(self) -> PowerLimit | None:
        data = self.coordinator.data
        return data.power_limits.get(self.entity_description.limit_key) if data else None

    @property
    def native_value(self) -> float | None:
        if (limit := self._limit) is None:
            return None
        return limit.percent if self.entity_description.as_percent else limit.watts

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        runtime = self._runtime
        key = self.entity_description.limit_key
        if self.entity_description.as_percent:
            command = runtime.client.set_power_limit_percent(runtime.hub_id, key, round(value))
        else:
            limit = self._limit
            command = runtime.client.set_power_limit_watts(
                runtime.hub_id,
                key,
                round(value),
                percent_based=limit is not None and limit.is_percent_based,
            )
        await async_run_command(self.hass, self.coordinator.config_entry, command)
        await self.coordinator.async_request_refresh()


class AnodeOverrideDurationNumber(RestoreNumber):
    """How long the Override mode select overrides the schedule for.

    Stored in Home Assistant only; the hub has no such setting.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "override_duration"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = MAX_OVERRIDE_DURATION_MIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_should_poll = False

    def __init__(self, runtime: AnodeRuntimeData) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.hub_id}_override_duration"
        self._attr_device_info = device_info(runtime.hub_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._runtime.override_duration_min = round(last.native_value)

    @property
    def native_value(self) -> float:
        return self._runtime.override_duration_min

    async def async_set_native_value(self, value: float) -> None:
        self._runtime.override_duration_min = round(value)
        self.async_write_ha_state()

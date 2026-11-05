"""Hub lifetime energy totals, counted on from device counters.

A hub total such as house energy is made of device lifetime counters. Their
sum would step whenever a device joined or left it or a counter reset, so
each total instead counts on by how far each counter moved since the last
read. A counter seen for the first time only sets where it counts from, one
that falls counts on from its new value, and one whose device leaves stops
contributing. Totals and the last value of every counter are stored, so
energy used while Home Assistant was stopped counts on the next read.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .api import HubStatus
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import Telemetry

STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = timedelta(minutes=5)

HOUSE_ENERGY: Final = "house_energy"
GRID_IMPORT_ENERGY: Final = "grid_import_energy"
GRID_EXPORT_ENERGY: Final = "grid_export_energy"
BATTERY_CHARGE_ENERGY: Final = "battery_cumulative_charge_energy"
BATTERY_DISCHARGE_ENERGY: Final = "battery_cumulative_discharge_energy"


@dataclass(frozen=True, slots=True)
class Counter:
    """One device lifetime counter's part in a total."""

    key: str
    sign: int
    kwh: float | None
    #: Whether a total's starting value includes it. Starting values are the
    #: sum of meter and battery counters, which is what a hub total showed
    #: before it was stored, so a restored sensor carries on without a step.
    in_start: bool = True


def _counters(
    readings: Mapping[str, Any],
    device_ids: Iterable[str],
    parts: tuple[tuple[str, int], ...],
    *,
    in_start: bool = True,
) -> list[Counter] | None:
    """The named counters of several devices; None if any has never reported."""
    counters: list[Counter] = []
    for device_id in device_ids:
        if (reading := readings.get(device_id)) is None:
            return None
        counters.extend(
            Counter(f"{device_id}:{attr}", sign, getattr(reading, attr), in_start)
            for attr, sign in parts
        )
    return counters


def _grid_counters(status: HubStatus, telemetry: Telemetry, attr: str) -> list[Counter] | None:
    if not status.grid_meters or not telemetry.meters_current:
        return None
    return _counters(telemetry.meters, (m.id for m in status.grid_meters), ((attr, 1),))


def _battery_counters(
    status: HubStatus, telemetry: Telemetry, attr: str
) -> list[Counter] | None:
    if not status.batteries or not telemetry.batteries_current:
        return None
    return _counters(telemetry.batteries, status.batteries, ((attr, 1),))


def _house_counters(status: HubStatus, telemetry: Telemetry) -> list[Counter] | None:
    """Net house consumption.

    house = (grid import - grid export)
          + (generation export - generation import)
          + (inverter AC export - inverter AC import)
          + (battery discharge - battery charge)

    Needs the latest meter read, and battery and inverter reads where there
    are any, to have succeeded.
    """
    grid = status.grid_meters
    if not grid or not telemetry.meters_current:
        return None
    if status.batteries and not telemetry.batteries_current:
        return None
    if status.inverters and not telemetry.inverters_current:
        return None
    supply = (("export_energy_kwh", 1), ("import_energy_kwh", -1))
    inverters = _counters(telemetry.inverters, status.inverters, supply, in_start=False)
    # The hub leaves an inverter's counters out until its first detailed read.
    # Counting on without them would miss what the inverter moved meanwhile.
    if inverters is None or any(counter.kwh is None for counter in inverters):
        return None
    grid_counters = _counters(
        telemetry.meters,
        (m.id for m in grid),
        (("import_energy_kwh", 1), ("export_energy_kwh", -1)),
    )
    generation = _counters(telemetry.meters, (m.id for m in status.generation_meters), supply)
    batteries = _counters(
        telemetry.batteries,
        status.batteries,
        (("discharge_energy_kwh", 1), ("charge_energy_kwh", -1)),
    )
    if grid_counters is None or generation is None or batteries is None:
        return None
    return [*grid_counters, *generation, *inverters, *batteries]


TOTALS: Final[dict[str, Callable[[HubStatus, Telemetry], list[Counter] | None]]] = {
    HOUSE_ENERGY: _house_counters,
    GRID_IMPORT_ENERGY: lambda s, t: _grid_counters(s, t, "import_energy_kwh"),
    GRID_EXPORT_ENERGY: lambda s, t: _grid_counters(s, t, "export_energy_kwh"),
    BATTERY_CHARGE_ENERGY: lambda s, t: _battery_counters(s, t, "charge_energy_kwh"),
    BATTERY_DISCHARGE_ENERGY: lambda s, t: _battery_counters(s, t, "discharge_energy_kwh"),
}


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.energy_totals.{entry_id}")


async def async_remove_energy_totals(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the stored totals of a config entry that is gone."""
    await _store(hass, entry_id).async_remove()


def _floats(data: Any) -> dict[str, float]:
    if not isinstance(data, dict):
        return {}
    return {
        str(key): float(value)
        for key, value in data.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


class EnergyTotals:
    """The hub lifetime energy totals of one config entry, and their store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise with nothing counted."""
        self._store = _store(hass, entry_id)
        self._totals: dict[str, float] = {}
        self._last: dict[str, dict[str, float]] = {}
        self._save_requested = False

    async def async_load(self) -> None:
        """Carry on from the stored totals, if there are any."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return
        self._totals = _floats(data.get("totals"))
        counters = data.get("counters")
        if isinstance(counters, dict):
            self._last = {str(key): _floats(last) for key, last in counters.items()}

    @callback
    def update(self, status: HubStatus, telemetry: Telemetry) -> dict[str, float]:
        """Count on from a telemetry read; return the totals it reaches.

        A total whose inputs this read cannot vouch for is left as it was and
        not returned, so what it missed is counted on the next read that can.
        """
        reached: dict[str, float] = {}
        for key, counters_fn in TOTALS.items():
            if (counters := counters_fn(status, telemetry)) is None:
                continue
            if (total := self._count(key, counters)) is not None:
                reached[key] = total
        self._async_schedule_save()
        return reached

    def _count(self, key: str, counters: list[Counter]) -> float | None:
        last = self._last.get(key, {})
        known = {c.key: c for c in counters if c.kwh is not None}
        if key in self._totals:
            total = self._totals[key]
            for counter in known.values():
                if (previous := last.get(counter.key)) is not None:
                    total += counter.sign * max(0.0, counter.kwh - previous)
        elif known:
            total = sum(c.sign * c.kwh for c in known.values() if c.in_start)
        else:
            return None
        listed = {counter.key for counter in counters}
        self._totals[key] = total
        self._last[key] = {
            **{k: v for k, v in last.items() if k in listed},
            **{k: c.kwh for k, c in known.items()},
        }
        return total

    async def async_save(self) -> None:
        """Write a pending save now, so an unloaded entry leaves none behind."""
        if self._save_requested:
            await self._store.async_save(self._data_to_save())

    @callback
    def _async_schedule_save(self) -> None:
        # Every request puts the write back, and reads come more often than
        # the delay, so ask once per write.
        if not self._save_requested:
            self._save_requested = True
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY.total_seconds())

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        self._save_requested = False
        return {"totals": self._totals, "counters": self._last}

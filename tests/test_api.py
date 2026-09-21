"""Tests for the Anode API client and response parsing."""
from __future__ import annotations

from datetime import time
from http import HTTPStatus

import aiohttp
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.anode_battery.api import (
    AnodeAuthError,
    AnodeClient,
    AnodeCommandError,
    AnodeConnectionError,
    AnodeForbiddenError,
    AnodeHubOfflineError,
    AnodeHubRefusedError,
    AnodeNotFoundError,
    AnodeRateLimitError,
    AnodeResponseError,
    AnodeUpdateInProgressError,
    MeterType,
    OperatingMode,
    PowerLimit,
    PowerLimitKey,
    ScheduleSlot,
    SocLimits,
)

from .common import (
    ACCOUNT,
    API_KEY,
    BATTERIES,
    EMAIL,
    HUB_ID,
    MAX_CHARGE,
    METERS,
    MODE,
    OTA_LATEST,
    OTA_PROGRESS,
    OVERRIDE,
    SCHEDULE,
    SET_CONFIG,
    SOC_CONFIG,
    STATUS,
    AnodeCloud,
    load_fixture,
)


@pytest.fixture
def client(hass: HomeAssistant, cloud: AnodeCloud) -> AnodeClient:
    """A client talking to the fake cloud."""
    return AnodeClient(async_get_clientsession(hass), EMAIL, API_KEY)


async def test_account_hub(client: AnodeClient) -> None:
    """The account's hub id and alias are read from /api/user/devices."""
    hub = await client.get_account_hub()
    assert hub.hub_id == HUB_ID
    assert hub.alias == "Home hub"


async def test_hub_status_and_metadata(client: AnodeClient) -> None:
    """Status lists devices; metadata adds aliases and meter purposes."""
    status = await client.get_hub_status(HUB_ID)
    assert status.online is True
    assert status.version == "2.4.1"
    assert set(status.batteries) == {"bat01", "bat02"}
    assert status.meters["ev001"].parent_meter == "grid1"
    assert status.meters["grid1"].meter_type is MeterType.PRIMARY

    status = status.with_metadata(await client.get_device_metadata(HUB_ID))
    assert status.alias == "Home hub"
    assert status.batteries["bat01"].alias == "Garage battery"
    assert [m.id for m in status.grid_meters] == ["grid1"]
    assert [m.id for m in status.generation_meters] == ["solar1"]


async def test_meter_purpose_alone_identifies_grid_and_solar(
    client: AnodeClient, cloud: AnodeCloud
) -> None:
    """Meters without a type are classified by their purpose."""
    status_json = load_fixture("status.json")
    for meter in status_json["meter"]:
        meter.pop("type")
    cloud.respond("GET", STATUS, json=status_json)

    status = (await client.get_hub_status(HUB_ID)).with_metadata(
        await client.get_device_metadata(HUB_ID)
    )
    assert [m.id for m in status.grid_meters] == ["grid1"]
    assert [m.id for m in status.generation_meters] == ["solar1"]


async def test_batteries_normalise_units(client: AnodeClient) -> None:
    """Energy counters in deci-watt-hours become kWh."""
    batteries = await client.get_batteries(HUB_ID)
    bat01 = batteries["bat01"]
    assert bat01.power_w == 1500
    assert bat01.soc_pct == 75
    assert bat01.charge_energy_kwh == pytest.approx(922.691)
    assert bat01.discharge_energy_kwh == pytest.approx(730.428)
    assert bat01.energy_capacity_wh == pytest.approx(6038.4)
    assert bat01.energy_remaining_wh == pytest.approx(4528.8)
    assert bat01.capacity_remaining_ah == pytest.approx(102)
    assert batteries["bat02"].power_w == -500


async def test_meters_normalise_units(client: AnodeClient) -> None:
    """Meter energy counters become kWh."""
    meters = await client.get_meters(HUB_ID)
    assert meters["grid1"].import_energy_kwh == pytest.approx(500)
    assert meters["grid1"].export_energy_kwh == pytest.approx(30)
    assert meters["grid1"].voltage_v == pytest.approx(240.1)
    assert meters["solar1"].power_w == -2500


async def test_kilowatt_power_and_unknown_energy_unit(
    client: AnodeClient, cloud: AnodeCloud
) -> None:
    """kW is converted; an unrecognised unit is ignored rather than guessed."""
    cloud.respond(
        "GET",
        METERS,
        json=[
            {
                "id": "grid1",
                "power": {"value": 1.5, "unit": "kW"},
                "importEnergy": {"value": 10, "unit": "furlongs"},
            }
        ],
    )
    meter = (await client.get_meters(HUB_ID))["grid1"]
    assert meter.power_w == 1500
    assert meter.import_energy_kwh is None


async def test_malformed_readings_are_skipped(client: AnodeClient, cloud: AnodeCloud) -> None:
    """One bad item does not discard the rest of a batch."""
    batteries = load_fixture("batteries.json")
    del batteries[1]["soc"]
    batteries.append({"power": {"value": 1, "unit": "W"}})
    cloud.respond("GET", BATTERIES, json=batteries)
    assert set(await client.get_batteries(HUB_ID)) == {"bat01"}


async def test_batch_read_rejected_by_hub(client: AnodeClient, cloud: AnodeCloud) -> None:
    """A hub failure object instead of a list raises."""
    cloud.respond("GET", BATTERIES, json={"status": False, "info": "Unknown ID"})
    with pytest.raises(AnodeCommandError, match="Unknown ID"):
        await client.get_batteries(HUB_ID)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (HTTPStatus.UNAUTHORIZED, AnodeAuthError),
        (HTTPStatus.FORBIDDEN, AnodeForbiddenError),
        (HTTPStatus.NOT_FOUND, AnodeNotFoundError),
        (HTTPStatus.REQUEST_TIMEOUT, AnodeHubOfflineError),
        (HTTPStatus.TOO_MANY_REQUESTS, AnodeRateLimitError),
        (HTTPStatus.INTERNAL_SERVER_ERROR, AnodeResponseError),
    ],
)
async def test_http_errors(
    client: AnodeClient, cloud: AnodeCloud, status: HTTPStatus, error: type[Exception]
) -> None:
    """HTTP status codes map to specific errors."""
    cloud.respond("GET", STATUS, status=status)
    with pytest.raises(error):
        await client.get_hub_status(HUB_ID)


@pytest.mark.parametrize("exc", [aiohttp.ClientConnectionError(), TimeoutError()])
async def test_connection_errors(
    client: AnodeClient, cloud: AnodeCloud, exc: BaseException
) -> None:
    """Transport failures raise AnodeConnectionError."""
    cloud.respond("GET", STATUS, exc=exc)
    with pytest.raises(AnodeConnectionError):
        await client.get_hub_status(HUB_ID)


async def test_invalid_json(client: AnodeClient, cloud: AnodeCloud) -> None:
    """A non-JSON body raises AnodeResponseError."""
    cloud.respond("GET", STATUS, text="<html>gateway error</html>")
    with pytest.raises(AnodeResponseError):
        await client.get_hub_status(HUB_ID)


async def test_account_without_hub(client: AnodeClient, cloud: AnodeCloud) -> None:
    """Accounts with no hub of their own get a 404."""
    cloud.respond("GET", ACCOUNT, status=HTTPStatus.NOT_FOUND, json={"message": "No hub"})
    with pytest.raises(AnodeNotFoundError):
        await client.get_account_hub()


async def test_mode(client: AnodeClient, cloud: AnodeCloud) -> None:
    """The mode string is parsed; unknown values become None."""
    assert await client.get_mode(HUB_ID) is OperatingMode.CHARGE
    cloud.respond("GET", MODE, json={"mode": "turbo"})
    assert await client.get_mode(HUB_ID) is None


async def test_schedule(client: AnodeClient) -> None:
    """Empty placeholder slots are dropped."""
    schedule = await client.get_schedule(HUB_ID)
    assert schedule == [
        ScheduleSlot(time(1), time(6), OperatingMode.CHARGE),
        ScheduleSlot(time(16), time(19), OperatingMode.DISCHARGE),
    ]


def test_slot_contains_matches_firmware() -> None:
    """Begin is inclusive, end exclusive, and slots may cross midnight."""
    overnight = ScheduleSlot(time(23), time(2), OperatingMode.CHARGE)
    assert overnight.contains(time(23))
    assert overnight.contains(time(1, 59, 59))
    assert not overnight.contains(time(2))
    assert not overnight.contains(time(12))


async def test_soc_limits_read_from_value(client: AnodeClient, cloud: AnodeCloud) -> None:
    """SOC limits come from the response's ``value`` list."""
    assert await client.get_soc_limits(HUB_ID) == {
        "bat01": SocLimits(10, 90),
        "bat02": SocLimits(20, 100),
    }
    soc = load_fixture("config_soc.json")
    soc["value"][1]["status"] = False
    cloud.respond("GET", SOC_CONFIG, json=soc)
    assert set(await client.get_soc_limits(HUB_ID)) == {"bat01"}


async def test_set_soc_limits(client: AnodeClient, cloud: AnodeCloud) -> None:
    """SOC limits are written per battery; invalid windows are refused locally."""
    await client.set_soc_limits(HUB_ID, "bat01", SocLimits(15, 95))
    assert cloud.calls("PUT", SET_CONFIG)[-1][1] == {"socConfig_bat01": {"minSoc": 15, "maxSoc": 95}}

    with pytest.raises(ValueError):
        await client.set_soc_limits(HUB_ID, "bat01", SocLimits(60, 40))
    assert len(cloud.calls("PUT", SET_CONFIG)) == 1


async def test_power_limit_formats(client: AnodeClient, cloud: AnodeCloud) -> None:
    """Percent-based and legacy watt-based limits both parse."""
    assert await client.get_power_limit(HUB_ID, PowerLimitKey.MAX_CHARGE) == PowerLimit(
        watts=4400, percent=100
    )
    cloud.respond("GET", MAX_CHARGE, json={"status": True, "key": "maxChargePower", "value": 2000})
    limit = await client.get_power_limit(HUB_ID, PowerLimitKey.MAX_CHARGE)
    assert limit == PowerLimit(watts=2000, percent=None)
    assert not limit.is_percent_based


async def test_set_power_limit_bodies(client: AnodeClient, cloud: AnodeCloud) -> None:
    """Writes use the shape each firmware generation understands."""
    await client.set_power_limit_percent(HUB_ID, PowerLimitKey.MAX_CHARGE, 50)
    await client.set_power_limit_watts(HUB_ID, PowerLimitKey.MAX_CHARGE, 3000, percent_based=True)
    await client.set_power_limit_watts(
        HUB_ID, PowerLimitKey.MAX_DISCHARGE, 2500, percent_based=False
    )
    assert [body for _, body in cloud.calls("PUT", SET_CONFIG)] == [
        {"maxChargePower": {"percent": 50}},
        {"maxChargePower": {"watts": 3000}},
        {"maxDischargePower": 2500},
    ]


async def test_config_write_rejected(client: AnodeClient, cloud: AnodeCloud) -> None:
    """A hub rejection of a config write raises."""
    cloud.respond("PUT", SET_CONFIG, json={"status": False, "info": "busy"})
    with pytest.raises(AnodeCommandError, match="busy"):
        await client.set_soc_limits(HUB_ID, "bat01", SocLimits(10, 90))


async def test_override_requests(client: AnodeClient, cloud: AnodeCloud) -> None:
    """Overrides send mode and timeout; cancel sends MATCH for zero seconds."""
    await client.set_override(HUB_ID, OperatingMode.CHARGE, 3600)
    await client.cancel_override(HUB_ID)
    sent = [dict(url.query) for url, _ in cloud.calls("PUT", OVERRIDE)]
    assert sent == [
        {"mode": "CHARGE", "timeout": "3600"},
        {"mode": "MATCH", "timeout": "0"},
    ]
    with pytest.raises(ValueError):
        await client.set_override(HUB_ID, OperatingMode.CHARGE, -1)


async def test_override_rejected(client: AnodeClient, cloud: AnodeCloud) -> None:
    """The hub's JSON acknowledgement is unwrapped and checked."""
    cloud.respond(
        "PUT",
        OVERRIDE,
        json={"mode": '{"status":false,"info":"Failed to propagate event"}'},
    )
    with pytest.raises(AnodeCommandError, match="propagate"):
        await client.set_override(HUB_ID, OperatingMode.IDLE, 60)


async def test_schedule_read_rejected(client: AnodeClient, cloud: AnodeCloud) -> None:
    """A failed schedule read raises."""
    cloud.respond("GET", SCHEDULE, json={"status": False, "info": "flash error"})
    with pytest.raises(AnodeCommandError):
        await client.get_schedule(HUB_ID)


async def test_error_body_is_kept(client: AnodeClient, cloud: AnodeCloud) -> None:
    """An error carries its status and JSON body for callers that read them."""
    cloud.respond(
        "GET", STATUS, status=HTTPStatus.FORBIDDEN, json={"message": "no", "required": "device:read"}
    )
    with pytest.raises(AnodeForbiddenError) as err:
        await client.get_hub_status(HUB_ID)
    assert err.value.status == HTTPStatus.FORBIDDEN
    assert err.value.body == {"message": "no", "required": "device:read"}


async def test_update_firmware(client: AnodeClient, cloud: AnodeCloud) -> None:
    """One device is named, and what the server sent comes back."""
    result = await client.update_firmware(HUB_ID, HUB_ID)
    assert result.stream == "stable"
    assert [(u.device_id, u.from_version, u.to_version) for u in result.updates] == [
        (HUB_ID, "2.4.1", "2.5.0")
    ]
    (url, _), *_ = cloud.calls("PUT", OTA_LATEST)
    assert dict(url.query) == {"id": HUB_ID}


async def test_update_firmware_nothing_sent(client: AnodeClient, cloud: AnodeCloud) -> None:
    """Empty updates means the device was current and nothing was sent."""
    cloud.respond(
        "PUT",
        OTA_LATEST,
        json={"status": True, "info": "already up to date", "stream": "stable", "updates": []},
    )
    assert (await client.update_firmware(HUB_ID, "bat01")).updates == ()


@pytest.mark.parametrize(
    ("body", "device_id"),
    [
        ({"message": "update in progress", "id": "bat02"}, "bat02"),
        # The hub's own refusal does not say which device is busy.
        ({"message": "update in progress"}, None),
        (None, None),
    ],
)
async def test_update_firmware_in_progress(
    client: AnodeClient, cloud: AnodeCloud, body: dict | None, device_id: str | None
) -> None:
    """409 names the device updating when the server knows it."""
    cloud.respond("PUT", OTA_LATEST, status=HTTPStatus.CONFLICT, json=body)
    with pytest.raises(AnodeUpdateInProgressError) as err:
        await client.update_firmware(HUB_ID, "grid1")
    assert err.value.device_id == device_id


async def test_update_firmware_refused_by_hub(client: AnodeClient, cloud: AnodeCloud) -> None:
    """502 passes on what the hub said."""
    cloud.respond(
        "PUT",
        OTA_LATEST,
        status=HTTPStatus.BAD_GATEWAY,
        json={"message": "hub refused the update", "info": "Unknown device ID"},
    )
    with pytest.raises(AnodeHubRefusedError) as err:
        await client.update_firmware(HUB_ID, "grid1")
    assert err.value.info == "Unknown device ID"
    assert isinstance(err.value, AnodeCommandError)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (HTTPStatus.FORBIDDEN, AnodeForbiddenError),
        (HTTPStatus.NOT_FOUND, AnodeNotFoundError),
        (HTTPStatus.REQUEST_TIMEOUT, AnodeHubOfflineError),
    ],
)
async def test_update_firmware_other_errors(
    client: AnodeClient, cloud: AnodeCloud, status: HTTPStatus, error: type[Exception]
) -> None:
    """Everything else maps as for any other request."""
    cloud.respond("PUT", OTA_LATEST, status=status, json={"message": "no"})
    with pytest.raises(error):
        await client.update_firmware(HUB_ID, "grid1")


async def test_ota_progress(client: AnodeClient, cloud: AnodeCloud) -> None:
    """The percent is read, and clamped to 0-100."""
    assert await client.get_ota_progress(HUB_ID) == 42
    cloud.respond("GET", OTA_PROGRESS, json={"status": True, "percent": 140})
    assert await client.get_ota_progress(HUB_ID) == 100
    cloud.respond("GET", OTA_PROGRESS, json={"status": True, "info": "OTA in progress"})
    assert await client.get_ota_progress(HUB_ID) is None


async def test_ota_progress_quiet_hub(client: AnodeClient, cloud: AnodeCloud) -> None:
    """408 means no report arrived while the server waited, not an offline hub."""
    cloud.respond("GET", OTA_PROGRESS, status=HTTPStatus.REQUEST_TIMEOUT, json={"message": "timeout"})
    assert await client.get_ota_progress(HUB_ID) is None


async def test_update_firmware_malformed(client: AnodeClient, cloud: AnodeCloud) -> None:
    """No updates list is an error; unreadable entries are dropped."""
    cloud.respond("PUT", OTA_LATEST, json={"status": True})
    with pytest.raises(AnodeResponseError):
        await client.update_firmware(HUB_ID, "grid1")

    cloud.respond(
        "PUT",
        OTA_LATEST,
        json={"status": True, "updates": ["x", {"id": "a"}, {"id": "b", "from": "1", "to": "2"}]},
    )
    result = await client.update_firmware(HUB_ID, "b")
    assert [update.device_id for update in result.updates] == ["b"]
    assert result.stream is None


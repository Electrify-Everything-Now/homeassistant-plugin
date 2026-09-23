"""Tests for Anode update entities."""
from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.update import UpdateEntityFeature
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    CONF_API_KEY,
    CONF_EMAIL,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.anode_battery.const import (
    AUTH_LINK,
    CONF_AUTH_TYPE,
    CONF_FIRMWARE,
    CONF_HUB_ID,
    CONF_READ_ONLY,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    OTA_PROGRESS_INTERVAL,
    OTA_STATUS_INTERVAL,
)
from custom_components.anode_battery.update import UPDATE_INSTRUCTIONS

from .common import (
    EMAIL,
    HUB_ID,
    OTA_LATEST,
    OTA_PROGRESS,
    RELEASE_NOTES,
    STATUS,
    AnodeCloud,
    entity_id,
    load_fixture,
    refresh,
    state,
)


async def _release_notes(
    hass: HomeAssistant, ws_client: WebSocketGenerator, unique_id: str
) -> str | None:
    """Ask for an entity's release notes the way the dashboard does."""
    client = await ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "update/release_notes",
            "entity_id": entity_id(hass, "update", unique_id),
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


async def test_entities_created_only_where_the_server_answered(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A device the server has no firmware verdict for gets no entity."""
    registry = er.async_get(hass)
    for unique_id in (f"{HUB_ID}_firmware", "bat01_firmware", "grid1_firmware"):
        assert registry.async_get_entity_id("update", DOMAIN, unique_id)

    # ev001 is reported without latestVersion, so nothing is known about it.
    assert registry.async_get_entity_id("update", DOMAIN, "ev001_firmware") is None


async def test_update_available(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The hub and the meters are behind their train; the batteries are not."""
    hub = state(hass, "update", f"{HUB_ID}_firmware")
    assert hub.state == STATE_ON
    assert hub.attributes["installed_version"] == "2.4.1"
    assert hub.attributes["latest_version"] == "2.5.0"

    assert state(hass, "update", "grid1_firmware").state == STATE_ON

    battery = state(hass, "update", "bat01_firmware")
    assert battery.state == STATE_OFF
    assert battery.attributes["installed_version"] == "1.3.0"
    assert battery.attributes["latest_version"] == "1.3.0"


async def test_device_ahead_of_its_train_is_not_offered_a_downgrade(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """updateAvailable is the verdict, not latest != installed.

    A developer build sits ahead of the stable train. The versions differ, so
    Home Assistant's own comparison would light the badge; the server says no.
    """
    status = load_fixture("status.json")
    status["battery"][0]["version"] = "1.4.0-12-gabcdef1"
    status["battery"][0]["latestVersion"] = "1.3.0"
    status["battery"][0]["updateAvailable"] = False
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)

    entity = state(hass, "update", "bat01_firmware")
    assert entity.state == STATE_OFF
    assert entity.attributes["installed_version"] == "1.4.0-12-gabcdef1"
    assert entity.attributes["latest_version"] == "1.3.0"


async def test_ota_in_progress(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A flash started elsewhere shows here as installing."""
    assert state(hass, "update", "grid1_firmware").attributes["in_progress"] is False

    status = load_fixture("status.json")
    status["meter"][0]["otaInProgress"] = True
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)

    assert state(hass, "update", "grid1_firmware").attributes["in_progress"] is True


async def test_entity_appears_when_the_train_gains_an_image(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A device the server could not answer for gets an entity once it can."""
    assert er.async_get(hass).async_get_entity_id("update", DOMAIN, "ev001_firmware") is None

    status = load_fixture("status.json")
    status["meter"][2]["latestVersion"] = "1.2.0"
    status["meter"][2]["updateAvailable"] = True
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)

    assert state(hass, "update", "ev001_firmware").state == STATE_ON


async def test_older_api_creates_no_update_entities(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A deployment without the firmware fields is inert, not broken."""
    status = load_fixture("status.json")
    status["hub"].pop("latestVersion")
    status["hub"].pop("updateAvailable")
    for device in (*status["battery"], *status["meter"], *status["repeater"]):
        device.pop("latestVersion", None)
        device.pop("updateAvailable", None)
    cloud.respond("GET", STATUS, json=status)

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert not [
        entry
        for entry in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
        if entry.domain == "update"
    ]


async def test_unreachable_hub_makes_entities_unavailable(
    hass: HomeAssistant, init_integration: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A failed status read leaves nothing claiming to know a version."""
    cloud.respond("GET", STATUS, status=HTTPStatus.REQUEST_TIMEOUT)
    await refresh(hass, init_integration.runtime_data.status)
    assert state(hass, "update", f"{HUB_ID}_firmware").state == STATE_UNAVAILABLE


async def test_release_notes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    cloud: AnodeCloud,
) -> None:
    """Notes are fetched for the offered version and end with how to apply it."""
    notes = await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware")
    assert notes is not None
    assert "Faster grid response" in notes
    assert "What's new" in notes
    assert "Fixed a stall" in notes
    assert UPDATE_INSTRUCTIONS in notes

    sent = cloud.calls("GET", RELEASE_NOTES)
    assert dict(sent[-1][0].query) == {"version": "2.5.0", "productCode": "1"}


async def test_repeater_release_notes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    cloud: AnodeCloud,
) -> None:
    """A repeater's notes are asked for under the repeater product code."""
    status = load_fixture("status.json")
    status["repeater"][0]["latestVersion"] = "v0.3.0"
    status["repeater"][0]["updateAvailable"] = True
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)
    assert state(hass, "update", "rep01_firmware").state == STATE_ON

    await _release_notes(hass, hass_ws_client, "rep01_firmware")
    sent = cloud.calls("GET", RELEASE_NOTES)
    assert dict(sent[-1][0].query) == {"version": "v0.3.0", "productCode": "4"}


async def test_release_notes_are_cached_per_version(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    cloud: AnodeCloud,
) -> None:
    """Reopening the dialog does not ask the cloud again."""
    await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware")
    after_first = len(cloud.calls("GET", RELEASE_NOTES))
    await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware")
    assert len(cloud.calls("GET", RELEASE_NOTES)) == after_first


async def test_release_notes_without_a_published_note(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    cloud: AnodeCloud,
) -> None:
    """No note published still tells the person where to apply the update."""
    cloud.respond("GET", RELEASE_NOTES, status=HTTPStatus.NOT_FOUND, json={})
    notes = await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware")
    assert notes == UPDATE_INSTRUCTIONS


async def test_release_notes_when_the_server_stops_answering(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    cloud: AnodeCloud,
) -> None:
    """An entity outliving its verdict has no version to fetch notes for.

    The OTA share becoming unreadable drops latestVersion from a device that
    already has an entity. There is nothing to ask about, so nothing is asked.
    """
    status = load_fixture("status.json")
    status["hub"].pop("latestVersion")
    status["hub"].pop("updateAvailable")
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, init_integration.runtime_data.status)

    before = len(cloud.calls("GET", RELEASE_NOTES))
    assert await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware") is None
    assert len(cloud.calls("GET", RELEASE_NOTES)) == before


async def test_release_notes_survive_a_failed_lookup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    cloud: AnodeCloud,
) -> None:
    """A broken notes endpoint does not break the dialog, and is retried."""
    cloud.respond("GET", RELEASE_NOTES, status=HTTPStatus.INTERNAL_SERVER_ERROR, json={})
    assert await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware") == UPDATE_INSTRUCTIONS

    # Nothing was cached, so a recovered endpoint is picked up.
    cloud.respond("GET", RELEASE_NOTES, json=load_fixture("release_note.json"))
    notes = await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware")
    assert notes is not None
    assert "Faster grid response" in notes



# Installing -------------------------------------------------------------------


@pytest.fixture
async def installable(hass: HomeAssistant, cloud: AnodeCloud) -> MockConfigEntry:
    """An entry linked with permission to install firmware."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home hub",
        unique_id=HUB_ID,
        version=1,
        minor_version=2,
        data={
            CONF_AUTH_TYPE: AUTH_LINK,
            CONF_EMAIL: EMAIL,
            CONF_API_KEY: "linked-api-key",
            CONF_HUB_ID: HUB_ID,
            CONF_READ_ONLY: False,
            CONF_FIRMWARE: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def status_with(**updating: bool) -> dict[str, Any]:
    """The status fixture with otaInProgress set on the devices named."""
    status = load_fixture("status.json")
    for device in (status["hub"], *status["battery"], *status["meter"]):
        device_id = device.get("id", HUB_ID)
        if device_id in updating:
            device["otaInProgress"] = updating[device_id]
    return status


def accept_grid1(cloud: AnodeCloud) -> None:
    """The server sending grid1 its update when asked for it."""
    cloud.respond(
        "PUT",
        OTA_LATEST,
        query={"id": "grid1"},
        json=load_fixture("ota_latest.json")
        | {"ids": ["grid1"], "updates": [{"id": "grid1", "from": "1.1.0", "to": "1.2.0"}]},
    )


async def install(hass: HomeAssistant, unique_id: str) -> None:
    """Press Install, the way the dashboard does."""
    await hass.services.async_call(
        "update",
        "install",
        {"entity_id": entity_id(hass, "update", unique_id)},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_install_offered_only_with_permission(
    hass: HomeAssistant, cloud: AnodeCloud, mock_config_entry: MockConfigEntry
) -> None:
    """A hand-made API key says nothing of what it may do, so no Install."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    features = state(hass, "update", f"{HUB_ID}_firmware").attributes[ATTR_SUPPORTED_FEATURES]
    assert not features & UpdateEntityFeature.INSTALL
    assert features & UpdateEntityFeature.PROGRESS


async def test_install_offered_to_a_linked_key_that_may(
    hass: HomeAssistant, installable: MockConfigEntry
) -> None:
    """With permission, every firmware entity can install, so HA lists it."""
    for unique_id in (f"{HUB_ID}_firmware", "bat01_firmware", "grid1_firmware"):
        features = state(hass, "update", unique_id).attributes[ATTR_SUPPORTED_FEATURES]
        assert features & UpdateEntityFeature.INSTALL


async def test_release_notes_skip_instructions_when_installable(
    hass: HomeAssistant,
    installable: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Notes do not send the person elsewhere when Install is right there."""
    notes = await _release_notes(hass, hass_ws_client, f"{HUB_ID}_firmware")
    assert notes is not None
    assert "Faster grid response" in notes
    assert UPDATE_INSTRUCTIONS not in notes


async def test_install(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Install names the one device, then shows it installing with progress."""
    runtime = installable.runtime_data
    cloud.respond("GET", STATUS, json=status_with(**{HUB_ID: True}))

    await install(hass, f"{HUB_ID}_firmware")

    (url, _), *rest = cloud.calls("PUT", OTA_LATEST)
    assert not rest
    assert dict(url.query) == {"id": HUB_ID}

    # Status is read again at once, and more often while the update runs.
    assert runtime.status.update_interval == OTA_STATUS_INTERVAL
    assert runtime.firmware.update_interval == OTA_PROGRESS_INTERVAL
    await refresh(hass, runtime.firmware)

    entity = state(hass, "update", f"{HUB_ID}_firmware")
    assert entity.attributes["in_progress"] is True
    assert entity.attributes["update_percentage"] == 42
    assert cloud.calls("GET", OTA_PROGRESS)


async def test_install_finishes(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Status reporting the new version ends the update and the extra polling."""
    accept_grid1(cloud)
    runtime = installable.runtime_data
    cloud.respond("GET", STATUS, json=status_with(grid1=True))
    await install(hass, "grid1_firmware")
    assert state(hass, "update", "grid1_firmware").attributes["in_progress"] is True

    done = load_fixture("status.json")
    done["meter"][0] |= {"version": "1.2.0", "updateAvailable": False}
    cloud.respond("GET", STATUS, json=done)
    await refresh(hass, runtime.status)

    entity = state(hass, "update", "grid1_firmware")
    assert entity.state == STATE_OFF
    assert entity.attributes["in_progress"] is False
    assert entity.attributes["update_percentage"] is None
    assert runtime.firmware.update_interval is None
    assert runtime.status.update_interval == timedelta(seconds=DEFAULT_STATUS_INTERVAL)


async def test_install_that_never_starts(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A status read begun after sending is the hub's answer, even if it is no."""
    accept_grid1(cloud)
    await install(hass, "grid1_firmware")

    # The status fixture never shows grid1 updating, so it failed at once.
    entity = state(hass, "update", "grid1_firmware")
    assert entity.attributes["in_progress"] is False
    assert installable.runtime_data.firmware.update_interval is None


async def test_install_survives_the_reboot(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A hub restarting to finish its update stays available, and installing."""
    cloud.respond("GET", STATUS, status=HTTPStatus.REQUEST_TIMEOUT)
    await install(hass, f"{HUB_ID}_firmware")

    entity = state(hass, "update", f"{HUB_ID}_firmware")
    assert entity.state == STATE_ON
    assert entity.attributes["in_progress"] is True


async def test_sent_update_shows_until_status_reports_it(
    hass: HomeAssistant,
    installable: MockConfigEntry,
    cloud: AnodeCloud,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without a status answer, a sent update shows as installing for a while."""
    accept_grid1(cloud)
    firmware = installable.runtime_data.firmware
    cloud.respond("GET", STATUS, status=HTTPStatus.REQUEST_TIMEOUT)
    await install(hass, "grid1_firmware")
    assert firmware.is_updating("grid1")

    freezer.tick(timedelta(minutes=3))
    assert not firmware.is_updating("grid1")


async def test_install_refused_while_another_device_updates(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """The hub takes one update at a time, so the person is told which."""
    cloud.respond("GET", STATUS, json=status_with(bat02=True))
    await refresh(hass, installable.runtime_data.status)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "bat02")})
    assert device is not None

    with pytest.raises(HomeAssistantError) as err:
        await install(hass, "grid1_firmware")
    assert err.value.translation_key == "update_in_progress"
    assert err.value.translation_placeholders == {"device": device.name}
    assert not cloud.calls("PUT", OTA_LATEST)


async def test_install_refused_while_one_is_being_sent(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A second press while the first is on its way is not sent."""
    async with installable.runtime_data.firmware.lock:
        with pytest.raises(HomeAssistantError) as err:
            await install(hass, "grid1_firmware")
    assert err.value.translation_key == "update_in_progress_hub"
    assert not cloud.calls("PUT", OTA_LATEST)


@pytest.mark.parametrize(
    ("body", "translation_key"),
    [
        ({"message": "update in progress", "id": "solar1"}, "update_in_progress"),
        ({"message": "update in progress"}, "update_in_progress_hub"),
    ],
)
async def test_install_refused_by_the_server(
    hass: HomeAssistant,
    installable: MockConfigEntry,
    cloud: AnodeCloud,
    body: dict[str, str],
    translation_key: str,
) -> None:
    """An update started elsewhere, that status had not shown yet."""
    cloud.respond("PUT", OTA_LATEST, status=HTTPStatus.CONFLICT, json=body)
    before = len(cloud.calls("GET", STATUS))

    with pytest.raises(HomeAssistantError) as err:
        await install(hass, "grid1_firmware")
    assert err.value.translation_key == translation_key
    # Status is read again so the entities catch up with the running update.
    await hass.async_block_till_done()
    assert len(cloud.calls("GET", STATUS)) > before
    assert state(hass, "update", "grid1_firmware").attributes["in_progress"] is False


async def test_install_refused_by_the_hub(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """What the hub said is passed on."""
    cloud.respond(
        "PUT",
        OTA_LATEST,
        status=HTTPStatus.BAD_GATEWAY,
        json={"message": "hub refused the update", "info": "Unknown device ID"},
    )
    with pytest.raises(HomeAssistantError) as err:
        await install(hass, "grid1_firmware")
    assert err.value.translation_key == "update_refused"
    assert err.value.translation_placeholders == {"info": "Unknown device ID"}


async def test_install_with_nothing_to_send(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Status was stale and the device is current: say so, not "installing"."""
    cloud.respond(
        "PUT",
        OTA_LATEST,
        json={"status": True, "info": "already up to date", "stream": "stable", "updates": []},
    )
    with pytest.raises(HomeAssistantError) as err:
        await install(hass, "grid1_firmware")
    assert err.value.translation_key == "update_not_needed"
    assert state(hass, "update", "grid1_firmware").attributes["in_progress"] is False


async def test_install_refused_for_an_offline_device(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """The server skips offline devices, so asking would only say "current"."""
    status = load_fixture("status.json")
    status["meter"][0]["online"] = False
    cloud.respond("GET", STATUS, json=status)
    await refresh(hass, installable.runtime_data.status)

    with pytest.raises(HomeAssistantError) as err:
        await install(hass, "grid1_firmware")
    assert err.value.translation_key == "update_device_offline"
    assert not cloud.calls("PUT", OTA_LATEST)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (HTTPStatus.FORBIDDEN, "not_permitted"),
        (HTTPStatus.REQUEST_TIMEOUT, "hub_offline_command"),
    ],
)
async def test_install_other_errors(
    hass: HomeAssistant,
    installable: MockConfigEntry,
    cloud: AnodeCloud,
    status: HTTPStatus,
    error: str,
) -> None:
    """Everything else reads as for any other command."""
    cloud.respond("PUT", OTA_LATEST, status=status, json={"message": "no"})
    with pytest.raises(HomeAssistantError) as err:
        await install(hass, "grid1_firmware")
    assert err.value.translation_key == error
    assert state(hass, "update", "grid1_firmware").attributes["in_progress"] is False


async def test_update_started_elsewhere_polls_progress(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """An update started from the app gets a progress bar here too."""
    runtime = installable.runtime_data
    cloud.respond("GET", STATUS, json=status_with(bat01=True))
    await refresh(hass, runtime.status)
    assert runtime.firmware.update_interval == OTA_PROGRESS_INTERVAL
    await refresh(hass, runtime.firmware)

    assert state(hass, "update", "bat01_firmware").attributes["update_percentage"] == 42
    assert state(hass, "update", "grid1_firmware").attributes["update_percentage"] is None


async def test_progress_between_reports(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """A quiet hub, or a failed read, keeps the last percent."""
    runtime = installable.runtime_data
    cloud.respond("GET", STATUS, json=status_with(bat01=True))
    await refresh(hass, runtime.status)
    await refresh(hass, runtime.firmware)

    for response in (
        {"status": HTTPStatus.REQUEST_TIMEOUT, "json": {"message": "timeout"}},
        {"status": HTTPStatus.INTERNAL_SERVER_ERROR, "json": {}},
    ):
        cloud.respond("GET", OTA_PROGRESS, **response)
        await refresh(hass, runtime.firmware)
        assert runtime.firmware.last_update_success
        assert state(hass, "update", "bat01_firmware").attributes["update_percentage"] == 42


async def test_no_progress_when_the_device_is_ambiguous(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """The hub's one percent cannot be split between two devices."""
    runtime = installable.runtime_data
    cloud.respond("GET", STATUS, json=status_with(bat01=True, bat02=True))
    await refresh(hass, runtime.status)
    await refresh(hass, runtime.firmware)

    for unique_id in ("bat01_firmware", "bat02_firmware"):
        entity = state(hass, "update", unique_id)
        assert entity.attributes["in_progress"] is True
        assert entity.attributes["update_percentage"] is None


async def test_progress_with_a_rejected_key_asks_to_reauthenticate(
    hass: HomeAssistant, installable: MockConfigEntry, cloud: AnodeCloud
) -> None:
    """Unlike a missed report, a rejected key is not something to wait out."""
    runtime = installable.runtime_data
    cloud.respond("GET", STATUS, json=status_with(bat01=True))
    await refresh(hass, runtime.status)
    cloud.respond("GET", OTA_PROGRESS, status=HTTPStatus.UNAUTHORIZED, json={"message": "no"})
    await refresh(hass, runtime.firmware)

    assert not runtime.firmware.last_update_success
    assert [
        flow for flow in hass.config_entries.flow.async_progress() if flow["context"]["source"] == "reauth"
    ]


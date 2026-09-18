"""Tests for Anode update entities."""
from __future__ import annotations

from http import HTTPStatus

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.anode_battery.const import DOMAIN
from custom_components.anode_battery.update import UPDATE_INSTRUCTIONS

from .common import (
    HUB_ID,
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
    for device in (*status["battery"], *status["meter"]):
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

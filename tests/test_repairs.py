"""Tests for the suggestion to link again so firmware can be installed."""
from __future__ import annotations

from typing import Any

import pytest

from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anode_battery.const import (
    AUTH_LINK,
    CONF_AUTH_TYPE,
    CONF_FIRMWARE,
    CONF_HUB_ID,
    CONF_READ_ONLY,
    DOMAIN,
)

from .common import EMAIL, HUB_ID, STATUS, AnodeCloud, load_fixture, refresh


def linked_entry(**data: Any) -> MockConfigEntry:
    """A linked entry, as saved before linking asked for firmware."""
    return MockConfigEntry(
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
            **data,
        },
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"firmware_permission_{entry.entry_id}")


def status_without_updates() -> dict[str, Any]:
    status = load_fixture("status.json")
    for device in (status["hub"], *status["battery"], *status["meter"]):
        if "updateAvailable" in device:
            device["updateAvailable"] = False
    return status


async def test_suggested_while_an_update_waits(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """The suggestion names the updates Home Assistant could have installed."""
    entry = await setup(hass, linked_entry())

    found = issue(hass, entry)
    assert found is not None
    assert found.translation_key == "firmware_permission"
    assert found.severity is ir.IssueSeverity.WARNING
    assert not found.is_fixable
    assert found.learn_more_url.endswith("#firmware-updates")
    assert found.translation_placeholders["hub"] == "Home hub"
    devices = found.translation_placeholders["devices"].splitlines()
    assert devices == [
        "- Home hub: 2.4.1 → 2.5.0",
        "- Grid: 1.1.0 → 1.2.0",
        "- Solar: 1.1.0 → 1.2.0",
    ]


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(linked_entry(**{CONF_FIRMWARE: True}), id="linked with firmware"),
        pytest.param(
            MockConfigEntry(
                domain=DOMAIN,
                unique_id=HUB_ID,
                version=1,
                minor_version=2,
                data={CONF_EMAIL: EMAIL, CONF_API_KEY: "key", CONF_HUB_ID: HUB_ID},
            ),
            id="api key",
        ),
    ],
)
async def test_not_suggested_where_linking_would_not_help(
    hass: HomeAssistant, cloud: AnodeCloud, entry: MockConfigEntry
) -> None:
    """Already allowed, or a key made by hand, possibly an installer's."""
    await setup(hass, entry)
    assert issue(hass, entry) is None


async def test_follows_updates_coming_and_going(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """Nothing waiting, nothing to suggest."""
    cloud.respond("GET", STATUS, json=status_without_updates())
    entry = await setup(hass, linked_entry())
    assert issue(hass, entry) is None

    cloud.respond("GET", STATUS, json=load_fixture("status.json"))
    await refresh(hass, entry.runtime_data.status)
    assert issue(hass, entry) is not None

    cloud.respond("GET", STATUS, json=status_without_updates())
    await refresh(hass, entry.runtime_data.status)
    assert issue(hass, entry) is None


async def test_ignored_stays_ignored_for_later_updates(
    hass: HomeAssistant, cloud: AnodeCloud
) -> None:
    """Someone who chose the app is not asked again at every release."""
    entry = await setup(hass, linked_entry())
    issue_id = f"firmware_permission_{entry.entry_id}"
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)

    cloud.respond("GET", STATUS, json=status_without_updates())
    await refresh(hass, entry.runtime_data.status)
    cloud.respond("GET", STATUS, json=load_fixture("status.json"))
    await refresh(hass, entry.runtime_data.status)

    found = issue(hass, entry)
    assert found is not None
    assert found.dismissed_version is not None


async def test_linking_again_clears_it(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    """Once the permission is there the suggestion goes, even an ignored one."""
    entry = await setup(hass, linked_entry())
    ir.async_ignore_issue(hass, DOMAIN, f"firmware_permission_{entry.entry_id}", True)

    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_FIRMWARE: True})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, entry) is None


async def test_removing_the_entry_clears_it(hass: HomeAssistant, cloud: AnodeCloud) -> None:
    entry = await setup(hass, linked_entry())
    assert issue(hass, entry) is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, entry) is None

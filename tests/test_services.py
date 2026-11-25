"""Tests for the Express One services (track_parcel / untrack_parcel)."""

from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.express_one.const import (
    CONF_COUNTRY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.express_one.services import async_setup_services

from .payloads import ACTIVE_CODE, OTHER_CODE, active_sample

_SAMPLE = active_sample()
_PATCH = "custom_components.express_one.api.ExpressOneApiClient.async_get_parcel"


async def _setup(
    hass, parcels: list[dict] | None = None, country: str = "BG"
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{country}",
        title=f"Express One {country}",
        data={CONF_COUNTRY: country},
        options={CONF_PARCELS: parcels or []},
    )
    entry.add_to_hass(hass)
    with patch(_PATCH, new=AsyncMock(return_value=_SAMPLE)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_SAMPLE)):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_removes_spaces_from_the_code(hass):
    entry = await _setup(hass)
    await _call(
        hass, "track_parcel", {CONF_TRACKING_CODE: "1582 7880 0000 0000 0000 1111"}
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ""}, blocking=True
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    for _ in range(2):
        await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert len(entry.options[CONF_PARCELS]) == 1


async def test_track_parcel_without_an_entry_raises(hass):
    entry = await _setup(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_two_entries_need_a_country_to_choose(hass):
    croatia = await _setup(hass, country="HR")
    bulgaria = await _setup(hass, country="BG")

    with pytest.raises(ServiceValidationError, match="pass country"):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )

    await _call(
        hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "hr"}
    )
    assert croatia.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert bulgaria.options[CONF_PARCELS] == []


async def test_country_without_an_entry_raises(hass):
    await _setup(hass, country="HR")
    with pytest.raises(ServiceValidationError, match="No Express One entry"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "BG"},
            blocking=True,
        )


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: OTHER_CODE})
    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_removes_the_code_from_every_entry_that_has_it(hass):
    parcels = [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    croatia = await _setup(hass, parcels, country="HR")
    bulgaria = await _setup(hass, parcels, country="BG")
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert croatia.options[CONF_PARCELS] == []
    assert bulgaria.options[CONF_PARCELS] == []


async def test_unloading_one_entry_keeps_the_services_for_the_other(hass):
    croatia = await _setup(hass, country="HR")
    bulgaria = await _setup(hass, country="BG")

    assert await hass.config_entries.async_unload(croatia.entry_id)
    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(bulgaria.entry_id)
    assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_update_contents_goes_to_every_entry_that_tracks_the_code(hass):
    parcels = [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    croatia = await _setup(hass, parcels, country="HR")
    bulgaria = await _setup(hass, parcels, country="BG")

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    for entry in (croatia, bulgaria):
        assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) == (
            "Running shoes"
        )


async def test_update_contents_for_an_untracked_code_needs_one_entry(hass):
    await _setup(hass, country="HR")
    await _setup(hass, country="BG")
    with pytest.raises(ServiceValidationError, match="No account lists"):
        await hass.services.async_call(
            DOMAIN,
            "update_contents",
            {CONF_TRACKING_CODE: OTHER_CODE, "contents": "x"},
            blocking=True,
        )


async def test_update_contents_rejects_a_blank_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError, match="required"):
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: "  "}, blocking=True
        )


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    other = await _setup(hass, country="HR")
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.config_entries.async_unload(other.entry_id)
    # services are gone with the last entry; register them as a stale call would
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError, match="not set up|not loaded"):
        await hass.services.async_call(
            DOMAIN,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "x"},
            blocking=True,
        )


async def test_services_without_any_entry_raise(hass):
    async_setup_services(hass)
    for service in ("track_parcel", "untrack_parcel"):
        with pytest.raises(ServiceValidationError, match="not set up"):
            await hass.services.async_call(
                DOMAIN, service, {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
            )


async def test_a_stale_entry_that_is_not_loaded_is_skipped_on_untrack(hass):
    parcels = [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    croatia = await _setup(hass, parcels, country="HR")
    bulgaria = await _setup(hass, parcels, country="BG")
    await hass.config_entries.async_unload(croatia.entry_id)

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert croatia.options[CONF_PARCELS] == []
    assert bulgaria.options[CONF_PARCELS] == []


async def test_track_with_contents_on_an_unloaded_entry_raises(hass):
    croatia = await _setup(hass, country="HR")
    await _setup(hass, country="BG")
    await hass.config_entries.async_unload(croatia.entry_id)
    with pytest.raises(ServiceValidationError, match="not set up|not loaded"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_COUNTRY: "HR", "contents": "x"},
            blocking=True,
        )

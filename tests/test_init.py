"""Tests for Express One setup and unload."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.express_one.api import ExpressOneApiError
from custom_components.express_one.const import CONF_PARCELS, CONF_TRACKING_CODE, DOMAIN

from .payloads import ACTIVE_CODE, OTHER_CODE, active_sample as _sample


async def test_setup_and_unload(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_BG",
        title="Express One Bulgaria",
        data={"country": "BG"},
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.express_one.api.ExpressOneApiClient.async_get_parcel",
        new=AsyncMock(return_value=_sample()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    # The active parcel produced a per-parcel sensor and the summary sensor.
    incoming = hass.states.get("sensor.express_one_bulgaria_incoming_parcels")
    assert incoming is not None
    assert incoming.state == "1"

    # Services registered on setup...
    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    # ...and removed when the last entry unloads.
    assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_setup_retries_when_first_refresh_fails(hass):
    """When the first data fetch fails, setup retries from the entry itself.

    The first refresh runs in __init__.py before platforms are forwarded, so a
    failure raises ConfigEntryNotReady from the entry setup (SETUP_RETRY) rather
    than — too late — from a forwarded platform.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_BG",
        title="Express One Bulgaria",
        data={"country": "BG"},
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.express_one.api.ExpressOneApiClient.async_get_parcel",
        new=AsyncMock(side_effect=ExpressOneApiError("Express One unreachable")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_per_parcel_sensor_spawn_and_remove(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_BG",
        title="Express One Bulgaria",
        data={"country": "BG"},
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    mock = AsyncMock(return_value=_sample())
    with patch(
        "custom_components.express_one.api.ExpressOneApiClient.async_get_parcel",
        new=mock,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{ACTIVE_CODE}"
        )

        # The next poll returns a different tracking code: the summary sensor
        # spawns a new per-parcel sensor and removes the stale one.
        mock.return_value = _sample(OTHER_CODE)
        await entry.runtime_data.coordinator.async_request_refresh()
        await hass.async_block_till_done()

        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{OTHER_CODE}"
        )
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{ACTIVE_CODE}"
            )
            is None
        )


async def test_options_update_applies_live_without_reload(hass):
    """Adding a parcel via options refreshes the coordinator immediately."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_BG",
        title="Express One Bulgaria",
        data={"country": "BG"},
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    mock = AsyncMock(return_value=_sample())
    with patch(
        "custom_components.express_one.api.ExpressOneApiClient.async_get_parcel",
        new=mock,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock.side_effect = lambda code: _sample(code)
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_PARCELS: [
                    {CONF_TRACKING_CODE: ACTIVE_CODE},
                    {CONF_TRACKING_CODE: OTHER_CODE},
                ],
            },
        )
        await hass.async_block_till_done()

    incoming = hass.states.get("sensor.express_one_bulgaria_incoming_parcels")
    assert incoming.state == "2"


async def test_entry_write_without_an_options_change_does_not_refresh(hass):
    """A write that changed no option must not poll.

    Home Assistant notifies the update listener on every
    ``async_update_entry``, and ``async_request_refresh`` ignores the "Enable
    polling for updates" system option — so a coordinator that persists a
    token or a learned identifier onto the entry must not turn that write into
    a poll.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_BG",
        title="Express One Bulgaria",
        data={"country": "BG"},
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.express_one.api.ExpressOneApiClient.async_get_parcel",
        new=AsyncMock(return_value=_sample()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        refresh = AsyncMock()
        with patch.object(
            entry.runtime_data.coordinator, "async_request_refresh", new=refresh
        ):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "persisted_by_the_coordinator": "value"}
            )
            await hass.async_block_till_done()
            assert refresh.call_count == 0

            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    CONF_PARCELS: [
                        {CONF_TRACKING_CODE: ACTIVE_CODE},
                        {CONF_TRACKING_CODE: OTHER_CODE},
                    ],
                },
            )
            await hass.async_block_till_done()
            assert refresh.call_count == 1


async def _setup_with_mock_client(hass, country: str):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{country}",
        data={"country": country},
        options={CONF_PARCELS: []},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.express_one.ExpressOneApiClient") as client_class,
        patch(
            "custom_components.express_one.build_ssl_context",
            return_value="context",
        ) as build,
    ):
        client_class.return_value.async_get_parcel = AsyncMock(return_value=None)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return client_class, build


async def test_bulgarian_entry_gets_the_extended_tls_context(hass):
    client_class, build = await _setup_with_mock_client(hass, "BG")

    build.assert_called_once()
    session, country, context = client_class.call_args.args
    assert (country, context) == ("BG", "context")
    # The session keeps no cookies: the tracker's session cookie is sent by hand.
    assert type(session.cookie_jar).__name__ == "DummyCookieJar"


async def test_croatian_entry_needs_no_tls_context(hass):
    client_class, build = await _setup_with_mock_client(hass, "HR")

    build.assert_not_called()
    assert client_class.call_args.args[1:] == ("HR", None)

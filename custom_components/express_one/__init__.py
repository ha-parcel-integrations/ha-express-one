"""Express One parcel tracker custom component for Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ExpressOneApiClient
from .const import CONF_COUNTRY, COUNTRY_BULGARIA, DOMAIN, PLATFORMS
from .contents import ContentsStore
from .coordinator import ExpressOneCoordinator
from .services import async_setup_services, async_unload_services
from .tls import build_ssl_context

_LOGGER = logging.getLogger(__name__)


@dataclass
class ExpressOneData:
    """Runtime data attached to the Express One config entry."""

    client: ExpressOneApiClient
    coordinator: ExpressOneCoordinator
    # The options as last applied, so an entry write that changed no option
    # is not mistaken for an options edit — see _async_options_updated.
    options: Mapping[str, object] = field(default_factory=dict)


type ExpressOneConfigEntry = ConfigEntry[ExpressOneData]


async def async_setup_entry(hass: HomeAssistant, entry: ExpressOneConfigEntry) -> bool:
    """Set up Express One from a config entry."""
    # No auth: tracking is public. The session keeps no cookies: the Bulgarian
    # tracker's session cookie is read and sent by hand, one per lookup.
    country = entry.data[CONF_COUNTRY]
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    ssl_context = None
    if country == COUNTRY_BULGARIA:
        # Loading the root certificates reads the disk.
        ssl_context = await hass.async_add_executor_job(build_ssl_context)
    client = ExpressOneApiClient(session, country, ssl_context)
    coordinator = ExpressOneCoordinator(hass, client, entry)

    await coordinator.contents_store.async_load()

    # Fetch initial data here, before forwarding to platforms. Raising
    # ConfigEntryNotReady from a forwarded platform is too late for HA to catch
    # cleanly (it logs a warning and half-sets-up the entry); doing the first
    # refresh here lets a transient failure fail the whole entry so HA retries
    # it with backoff.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = ExpressOneData(
        client=client, coordinator=coordinator, options=dict(entry.options)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Apply option changes (added/removed parcels, history) live via a
    # coordinator refresh — no reload — so per-parcel sensors appear and
    # disappear immediately. The update listener does NOT reload, so it does
    # not trip the config-entry-listener deprecation. This is also the resume
    # path after polling fully suspended (Section 2.1): adding a parcel back
    # triggers this refresh, which recomputes the tier and re-arms scheduling.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async_setup_services(hass)

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ExpressOneConfigEntry
) -> None:
    """Apply changed options by refreshing the coordinator.

    Home Assistant notifies an update listener on *every* ``async_update_entry``,
    not just an options edit, so the options are compared first. A carrier whose
    coordinator persists something onto the entry (a rotated token, a learned
    identifier) would otherwise poll on that write's cadence — and
    ``async_request_refresh`` goes straight past the "Enable polling for
    updates" system option, which only gates the *scheduled* refresh.
    """
    data = entry.runtime_data
    if entry.options == data.options:
        return
    data.options = dict(entry.options)
    await data.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ExpressOneConfigEntry) -> bool:
    """Unload the Express One config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    # One entry per country: the services stay while another entry is loaded.
    if not any(
        other.entry_id != entry.entry_id and other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        async_unload_services(hass)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ExpressOneConfigEntry) -> None:
    """Delete the contents the user set for the removed entry."""
    await ContentsStore(hass, entry.entry_id).async_remove()

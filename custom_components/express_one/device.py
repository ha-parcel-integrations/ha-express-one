"""The device every entity of this integration belongs to.

One place, because sensors, the button and the calendar must all land on the
*same* device entry. Each country entry is its own device.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_COUNTRY, COUNTRY_BULGARIA, COUNTRY_CROATIA, DOMAIN

CONFIGURATION_URLS = {
    COUNTRY_BULGARIA: "https://expressone.bg",
    COUNTRY_CROATIA: "https://tracking.expressone.hu",
}

ATTRIBUTION = "Data provided by Express One"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this hub."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Express One",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URLS.get(entry.data.get(CONF_COUNTRY, "")),
    )

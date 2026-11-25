"""Tests for Express One diagnostics."""

from datetime import timedelta
import json
from unittest.mock import MagicMock

import pytest

from custom_components.express_one.diagnostics import async_get_config_entry_diagnostics
from custom_components.express_one.parcels import normalize_parcel

from . import payloads as p


def _entry(parcels: list[dict]) -> MagicMock:
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": p.ACTIVE_CODE}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = parcels
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()
    return entry


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    parcel = normalize_parcel(p.delivered_sample(p.ACTIVE_CODE), include_history=True)
    parcel["contents"] = "Running shoes"
    result = await async_get_config_entry_diagnostics(hass, _entry([parcel]))

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    shown = result["incoming"][0]
    assert shown["barcode"] == "**REDACTED**"
    assert shown["url"] == "**REDACTED**"
    assert shown["contents"] == "**REDACTED**"
    assert shown["raw"]["lookup"] == "**REDACTED**"
    assert shown["raw"]["barcode"] == "**REDACTED**"
    assert shown["raw"]["events"][1]["location"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert shown["status"] == "delivered"
    assert shown["raw"]["events"][0]["code"] == "PRE"


@pytest.mark.parametrize(
    "raw",
    [p.delivered_sample(p.ACTIVE_CODE), p.bg_sample(p.BILL, p.ACTIVE_CODE)],
)
async def test_no_code_place_or_office_survives_in_either_tracker_payload(hass, raw):
    for event in raw["events"]:
        event["location"] = "Office 9 | SECRETTOWN"
    parcel = normalize_parcel(raw, include_history=True)

    result = await async_get_config_entry_diagnostics(hass, _entry([parcel]))

    dumped = json.dumps(result, default=str)
    for secret in (p.ACTIVE_CODE, p.BILL, "SECRETTOWN", "Office 9"):
        assert secret not in dumped


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = _entry([])
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }

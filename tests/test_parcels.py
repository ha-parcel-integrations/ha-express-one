"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping can be tested
as plain functions.
"""

from datetime import UTC, date, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.express_one.const import (
    CAPABILITIES_BY_VARIANT,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.express_one.parcels import (
    apply_delivered_filter,
    build_history,
    event_statuses,
    format_dimensions,
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from . import payloads as p
from .payloads import (
    active_sample,
    bg_event,
    bg_pickup_sample,
    bg_sample,
    delivered_sample,
    event,
    pickup_sample,
)

# ---------------------------------------------------------------------------
# status maps
# ---------------------------------------------------------------------------


def _hu_status(*codes: str) -> list[ParcelStatus | None]:
    return event_statuses(
        "hu", [event(code, "2026-04-29T10:00:00+02:00", "x") for code in codes]
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PRE", ParcelStatus.REGISTERED),
        ("INB", ParcelStatus.IN_TRANSIT),
        ("OUB", ParcelStatus.IN_TRANSIT),
        ("DEP", ParcelStatus.IN_TRANSIT),
        ("OFD", ParcelStatus.OUT_FOR_DELIVERY),
        ("S07", ParcelStatus.AT_PICKUP_POINT),
        ("E12", ParcelStatus.PROBLEM),
        ("D00", ParcelStatus.DELIVERED),
    ],
)
def test_hu_codes_map(code, expected):
    assert _hu_status(code) == [expected]


@pytest.mark.parametrize("code", ["E17", "E40", "E73", "E82", "E92", "I27"])
def test_hu_no_op_codes_map_to_nothing_without_a_warning(code, caplog):
    with caplog.at_level(logging.WARNING):
        assert _hu_status(code) == [None]
    assert caplog.text == ""


def test_dls_is_registered_only_before_anything_but_the_announcement():
    assert _hu_status("DLS") == [ParcelStatus.REGISTERED]
    assert _hu_status("PRE", "DLS") == [ParcelStatus.REGISTERED] * 2
    assert _hu_status("INB", "DLS") == [ParcelStatus.IN_TRANSIT] * 2
    # a hub scan before the upload makes it a scan on the way, wherever it sits
    assert _hu_status("PRE", "INB", "DLS", "DLS")[2:] == [ParcelStatus.IN_TRANSIT] * 2


@pytest.mark.parametrize(
    ("wording", "expected"),
    [
        ("Data received - shipment pending", ParcelStatus.REGISTERED),
        ("HUB Inbound", ParcelStatus.IN_TRANSIT),
        ("Parcel is in ExpressOne office", ParcelStatus.IN_TRANSIT),
        ("The shipment has been loaded to the van", ParcelStatus.OUT_FOR_DELIVERY),
        ("Not delivered", ParcelStatus.PROBLEM),
        ("Cancelled", ParcelStatus.PROBLEM),
        ("Available for office pickup", ParcelStatus.AT_PICKUP_POINT),
        ("Confirmed for delivery by PUDO", ParcelStatus.AT_PICKUP_POINT),
        ("Confirmed for delivery by PUP", ParcelStatus.AT_PICKUP_POINT),
        ("Delivered", ParcelStatus.DELIVERED),
    ],
)
def test_bg_wordings_map(wording, expected):
    assert event_statuses("bg", [bg_event(wording, "2026-04-29T10:00:00+03:00")]) == [
        expected
    ]


def test_bg_finalized_is_a_silent_no_op(caplog):
    with caplog.at_level(logging.WARNING):
        assert event_statuses(
            "bg", [bg_event("Finalized", "2026-04-29T10:00:00+03:00")]
        ) == [None]
    assert caplog.text == ""


def test_unmapped_code_warns_once_with_the_issue_link(caplog):
    with caplog.at_level(logging.WARNING):
        assert _hu_status("ZZZ", "ZZZ") == [None, None]
    assert caplog.text.count("ZZZ") == 1
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


def test_unmapped_bg_wording_warns_without_the_office(caplog):
    with caplog.at_level(logging.WARNING):
        event_statuses(
            "bg",
            [bg_event("Returned", "2026-04-29T10:00:00+03:00", "Office 1 | TOWN")],
        )
    assert "Returned" in caplog.text
    assert "TOWN" not in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_rounds_the_text():
    assert format_dimensions(27.9, 5.1, 27.9)["text"] == "28 x 5 x 28 cm"


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_is_oldest_first_with_wording_as_served():
    events = delivered_sample()["events"]
    history = build_history(events, event_statuses("hu", events))
    assert [entry["raw_status"] for entry in history] == [
        "Shipping details received",
        "Arrived at hub",
        "Out for delivery",
        "Successful delivery",
    ]
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    assert set(history[0]) == {"timestamp", "status", "raw_status"}


def test_build_history_caps_to_the_newest_events():
    events = [
        event("INB", f"2026-04-{day:02d}T10:00:00+02:00", f"move {day}")
        for day in range(1, 26)
    ]
    history = build_history(events, event_statuses("hu", events), max_events=20)
    assert len(history) == 20
    assert history[0]["raw_status"] == "move 6"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    for variant, fields in CAPABILITIES_BY_VARIANT.items():
        assert fields <= KNOWN_CAPABILITIES, variant


def test_capabilities_per_variant():
    assert list(CAPABILITIES_BY_VARIANT) == ["Croatia", "Bulgaria"]
    assert CAPABILITIES_BY_VARIANT["Croatia"] == {"url", "history"}
    assert CAPABILITIES_BY_VARIANT["Bulgaria"] == {"url", "history", "pickup_point"}


@pytest.mark.parametrize(
    ("variant", "samples"),
    [
        ("Croatia", (delivered_sample, active_sample, pickup_sample)),
        ("Bulgaria", (bg_sample, bg_pickup_sample)),
    ],
)
def test_capabilities_match_what_normalize_parcel_actually_returns(variant, samples):
    """Each variant's declared fields come true in its own samples, and every
    field left out stays ``None``."""
    capabilities = CAPABILITIES_BY_VARIANT[variant]
    parcels = [normalize_parcel(sample(), include_history=True) for sample in samples]

    for field in (
        "weight",
        "dimensions",
        "pickup_point",
        "url",
        "history",
        "contents",
    ):
        populated = any(parcel[field] is not None for parcel in parcels)
        assert populated is (field in capabilities), field
    window = any(
        parcel["planned_from"] is not None or parcel["planned_to"] is not None
        for parcel in parcels
    )
    assert window is ("delivery_window" in capabilities)


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Express One"
    assert parcel["barcode"] == p.DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Successful delivery"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == p.DELIVERED_AT
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert (
        parcel["url"]
        == f"https://tracking.expressone.hu/?plc_number={p.DELIVERED_CODE}"
    )
    assert parcel["pickup"] is False
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_failed_attempt_then_pickup():
    """The failure is replaced by the next event, so the parcel is not a problem."""
    raw = pickup_sample()
    raw["events"].insert(
        3, event("E12", "2026-04-29T11:00:00+02:00", "Delivery attempt failed")
    )
    assert normalize_parcel(raw)["status"] == ParcelStatus.AT_PICKUP_POINT
    raw["events"].pop()
    assert normalize_parcel(raw)["status"] == ParcelStatus.PROBLEM


def test_normalize_no_op_events_do_not_move_the_status():
    raw = active_sample()
    raw["events"].append(event("E82", "2026-04-29T09:00:00+02:00", "Schedule updated"))
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["raw_status"] == "Out for delivery"


def test_normalize_unmapped_newest_falls_back_with_one_warning(caplog):
    raw = active_sample()
    raw["events"].append(event("ZZZ", "2026-04-29T09:00:00+02:00", "Something new"))
    with caplog.at_level(logging.WARNING):
        first = normalize_parcel(raw)
        second = normalize_parcel(raw)
    assert first["status"] == second["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert caplog.text.count("ZZZ") == 1


def test_normalize_nothing_mappable_is_unknown_with_a_warning(caplog):
    raw = active_sample()
    raw["events"] = [event("E82", "2026-04-29T09:00:00+02:00", "Schedule updated")]
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["raw_status"] == "Schedule updated"
    assert "No Express One scan of a parcel sets a status yet" in caplog.text


def test_normalize_keeps_pickup_after_collection():
    raw = pickup_sample()
    raw["events"].append(event("D00", "2026-04-29T16:00:00+02:00", "Collected"))
    parcel = normalize_parcel(raw)
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True


def test_normalize_bulgarian_parcel_keeps_the_entered_code_as_barcode():
    parcel = normalize_parcel(bg_sample(p.BILL, p.ACTIVE_CODE))
    assert parcel["barcode"] == p.ACTIVE_CODE
    assert parcel["raw"]["bill_of_lading"] == p.BILL
    assert parcel["raw"]["lookup"] == p.ACTIVE_CODE
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["url"] == f"https://expressone.bg/en/tracking/{p.BILL}"
    assert parcel["delivered_at"] == "2026-04-29T15:39:00+03:00"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-answered code still yields a full parcel dict."""
    parcel = normalize_parcel(
        {"lookup": None, "barcode": "158278800000000000009999", "events": []}
    )
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["url"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


_AMS = ZoneInfo("Europe/Amsterdam")


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "2026-04-29T13:00:00+02:00",
            "2026-04-29T15:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            "2026-04-29T13:00:00+02:00",
            None,
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29T13:00:00+02:00",
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29T13:00:00",
            None,
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            None,
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            date(2026, 4, 29),
            date(2026, 4, 29),
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            "2026-05-01",
            ("2026-04-29T00:00:00+02:00", "2026-05-01T23:59:59+02:00"),
        ),
        (
            "2026-04-29T15:00:00+02:00",
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            None,
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            datetime(2026, 4, 29, 13, 0),
            datetime(2026, 4, 29, 15, 0),
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            "2026-04-29T23:30:00Z",
            None,
            ("2026-04-29T23:30:00Z", "2026-04-30T23:59:59+02:00"),
        ),
        (
            "2026-04-29T13:00:00+02:00",
            "2026-04-29",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            "2026-04-28T10:00:00+02:00",
            ("2026-04-28T10:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29T23:59:59+02:00",
            None,
            ("2026-04-29T23:59:59+02:00", None),
        ),
        (None, None, (None, None)),
        ("garbage", None, (None, None)),
        (12, 13, (None, None)),
    ],
)
def test_planned_window_shapes(start, end, expected):
    assert planned_window(start, end, zone=_AMS) == expected


@pytest.mark.parametrize(
    "status",
    [ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED],
)
def test_planned_for_status_empties_the_window(status):
    assert planned_for_status(status, "a", "b") == (None, None)


def test_planned_for_status_keeps_the_window_otherwise():
    for status in (
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.PROBLEM,
        ParcelStatus.UNKNOWN,
    ):
        assert planned_for_status(status, "a", "b") == ("a", "b")


def test_is_pickup_sources():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.AT_PICKUP_POINT) is True
    assert is_pickup(None, ParcelStatus.DELIVERED, [None, ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(False, ParcelStatus.IN_TRANSIT, [ParcelStatus.IN_TRANSIT]) is False
    assert is_pickup(None, ParcelStatus.DELIVERED) is False


def test_barcode_is_stable_when_the_source_switches_from_hu_to_bg():
    before = normalize_parcel(active_sample(p.ACTIVE_CODE))
    after = normalize_parcel(bg_sample(p.BILL, p.ACTIVE_CODE))
    assert before["barcode"] == after["barcode"] == p.ACTIVE_CODE


# ---------------------------------------------------------------------------
# delivered_at: first delivery of the final run
# ---------------------------------------------------------------------------


def test_delivered_at_is_the_first_of_a_repeated_delivered_scan():
    sample = delivered_sample()
    sample["events"] = [
        event("INB", "2026-04-28T15:52:17+02:00", "Arrived at hub"),
        event("D00", "2026-04-29T20:31:00+02:00", "Successful delivery"),
        event("I27", "2026-04-29T22:00:00+02:00", "x"),
        event("D00", "2026-04-30T20:31:00+02:00", "Successful delivery"),
    ]
    parcel = normalize_parcel(sample)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T20:31:00+02:00"


def test_delivered_at_restarts_after_a_later_non_delivered_event():
    sample = delivered_sample()
    sample["events"] = [
        event("D00", "2026-04-29T10:00:00+02:00", "Successful delivery"),
        event("OFD", "2026-04-30T08:00:00+02:00", "Out for delivery"),
        event("D00", "2026-04-30T12:00:00+02:00", "Successful delivery"),
        event("D00", "2026-04-30T13:00:00+02:00", "Successful delivery"),
    ]
    assert normalize_parcel(sample)["delivered_at"] == "2026-04-30T12:00:00+02:00"


def test_delivered_at_applies_to_the_bulgarian_source_too():
    sample = bg_sample()
    sample["events"] = [
        bg_event("Parcel is in ExpressOne office", "2026-04-28T08:05:00+03:00"),
        bg_event("Delivered", "2026-04-29T15:39:00+03:00", "Office 1 | TOWN"),
        bg_event("Finalized", "2026-04-29T16:00:00+03:00"),
        bg_event("Delivered", "2026-04-30T15:39:00+03:00", "Office 1 | TOWN"),
    ]
    assert normalize_parcel(sample)["delivered_at"] == "2026-04-29T15:39:00+03:00"


def test_delivered_at_is_none_while_not_delivered():
    assert normalize_parcel(active_sample())["delivered_at"] is None


# ---------------------------------------------------------------------------
# pickup_point (Bulgarian source only)
# ---------------------------------------------------------------------------


def _bg_pickup(*events: dict) -> dict:
    sample = bg_sample()
    sample["events"] = list(events)
    return normalize_parcel(sample)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("PUP 7005 | TOWN", "PUP 7005, TOWN"),
        ("  Офис 1- Е1  |  TOWN ", "Офис 1- Е1, TOWN"),
        ("Office 3 |", "Office 3"),
    ],
)
def test_pickup_point_formats_office_and_town(location, expected):
    parcel = _bg_pickup(
        bg_event("Available for office pickup", "2026-04-28T14:48:00+03:00", location)
    )
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == expected


def test_pickup_point_skips_an_event_without_an_office():
    parcel = _bg_pickup(
        bg_event(
            "Confirmed for delivery by PUDO",
            "2026-04-28T14:00:00+03:00",
            "Office 1 | TOWN",
        ),
        bg_event("Available for office pickup", "2026-04-28T14:48:00+03:00", "| TOWN"),
    )
    assert parcel["pickup_point"] == "Office 1, TOWN"


def test_pickup_point_is_none_when_no_event_names_an_office():
    parcel = _bg_pickup(
        bg_event("Available for office pickup", "2026-04-28T14:48:00+03:00", "| TOWN"),
        bg_event("Available for office pickup", "2026-04-28T15:00:00+03:00", None),
    )
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_pickup_point_prefers_the_pickup_event_over_the_delivered_one():
    parcel = _bg_pickup(
        bg_event(
            "Available for office pickup", "2026-04-28T14:48:00+03:00", "PUP 1 | TOWN"
        ),
        bg_event("Delivered", "2026-04-29T15:39:00+03:00", "Office 9 | TOWN"),
    )
    assert parcel["pickup_point"] == "PUP 1, TOWN"


def test_pickup_point_from_a_pup_confirmation():
    parcel = _bg_pickup(
        bg_event(
            "Confirmed for delivery by PUP", "2026-04-28T14:48:00+03:00", "PUP 2 | TOWN"
        ),
    )
    assert parcel["pickup_point"] == "PUP 2, TOWN"


def test_pickup_point_is_none_without_pickup():
    parcel = normalize_parcel(bg_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_pickup_point_stays_none_for_the_hungarian_source():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None

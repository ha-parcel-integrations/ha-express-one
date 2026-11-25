"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: the status maps and
:func:`normalize_parcel`. Everything else — the
timestamp parsing, the history builder, the sort contract, the delivered
filter, the one-shot warning for unmapped statuses — is suite-wide machinery
and should be left alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    BG_LOOKUP_URL,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-express-one/issues/new"
    "?template=unrecognised_status.yml"
)

# The Hungarian-side tracker keys on a three-character event code, the
# Bulgarian one on the wording before ``" - <office>"``. Prefer mapping too
# little over mapping wrongly: an unmapped value logs one warning asking the
# user to report it, which is how the maps grow.
_HU_STATUS_MAP: dict[str, ParcelStatus] = {
    "PRE": ParcelStatus.REGISTERED,
    "INB": ParcelStatus.IN_TRANSIT,
    "OUB": ParcelStatus.IN_TRANSIT,
    "DEP": ParcelStatus.IN_TRANSIT,
    "OFD": ParcelStatus.OUT_FOR_DELIVERY,
    "S07": ParcelStatus.AT_PICKUP_POINT,
    "E12": ParcelStatus.PROBLEM,
    "D00": ParcelStatus.DELIVERED,
}
_BG_STATUS_MAP: dict[str, ParcelStatus] = {
    "Data received - shipment pending": ParcelStatus.REGISTERED,
    "HUB Inbound": ParcelStatus.IN_TRANSIT,
    "Parcel is in ExpressOne office": ParcelStatus.IN_TRANSIT,
    "The shipment has been loaded to the van": ParcelStatus.OUT_FOR_DELIVERY,
    "Not delivered": ParcelStatus.PROBLEM,
    "Cancelled": ParcelStatus.PROBLEM,
    "Available for office pickup": ParcelStatus.AT_PICKUP_POINT,
    "Confirmed for delivery by PUDO": ParcelStatus.AT_PICKUP_POINT,
    "Confirmed for delivery by PUP": ParcelStatus.AT_PICKUP_POINT,
    "Delivered": ParcelStatus.DELIVERED,
}

# Events that carry no parcel state: the previous mappable event stands.
_HU_NO_OP_CODES = frozenset({"E17", "E40", "E73", "E82", "E92", "I27"})
_BG_NO_OP_WORDINGS = frozenset({"Finalized"})

# The data-uploaded scan: the first thing a parcel does after the sender's
# announcement, but also what a hub repeats later in the journey.
_HU_UPLOAD_CODE = "DLS"
_HU_ANNOUNCED_CODE = "PRE"

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier code or wording once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Express One status — help us map it. Open an issue "
        "and paste this line: %s\n  code or wording=%s → not used for the status",
        NEW_ISSUE_URL,
        code,
    )


def event_statuses(source: str | None, events: list[dict]) -> list[ParcelStatus | None]:
    """Map every event, oldest first; ``None`` for no-op and unmapped events.

    An unmapped code or wording warns once. A no-op event never moves the
    status, so the previous mappable event stands.
    """
    statuses: list[ParcelStatus | None] = []
    only_announced = True
    for event in events:
        status: ParcelStatus | None
        if source == "bg":
            key = str(event.get("wording") or "")
            no_op = key in _BG_NO_OP_WORDINGS
            status = _BG_STATUS_MAP.get(key)
        else:
            key = str(event.get("code") or "")
            no_op = key in _HU_NO_OP_CODES
            status = _HU_STATUS_MAP.get(key)
            if key == _HU_UPLOAD_CODE:
                # Registered only while nothing but the announcement came
                # before it; later it is a hub scan on the way.
                status = (
                    ParcelStatus.REGISTERED
                    if only_announced
                    else ParcelStatus.IN_TRANSIT
                )
            elif key != _HU_ANNOUNCED_CODE:
                only_announced = False
        if status is None and not no_op and key:
            _warn_unmapped_status(key)
        statuses.append(status)
    return statuses


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def build_history(
    events: list[dict],
    statuses: list[ParcelStatus | None],
    *,
    max_events: int = HISTORY_MAX_EVENTS,
) -> list[dict]:
    """Build the canonical ``history`` list from the events and their statuses.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the wording as served.
    Oldest → newest, capped to the most recent ``max_events``.
    """
    entries = [
        {
            "timestamp": event["timestamp"],
            "status": status,
            "raw_status": event.get("wording"),
        }
        for event, status in zip(events, statuses, strict=True)
    ]
    return entries[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def _page_url(raw: dict) -> str | None:
    """Return the tracker page for the lookup, or ``None`` before one answered."""
    lookup = raw.get("lookup")
    if not lookup:
        return None
    if raw.get("source") == "bg":
        return BG_LOOKUP_URL.format(number=raw.get("bill_of_lading") or lookup)
    if raw.get("source") == "hu":
        return tracking_url(lookup)
    return None


def _delivered_at(
    events: list[dict], statuses: list[ParcelStatus | None]
) -> str | None:
    """Return the first delivery of the final run of delivered events.

    A tracker can repeat its delivered scan; the moment of delivery is the
    first one after the last event that moved the parcel somewhere else.
    No-op and unmapped events do not break the run.
    """
    moment = None
    for event, status in zip(events, statuses, strict=True):
        if status is None:
            continue
        if status is not ParcelStatus.DELIVERED:
            moment = None
        elif moment is None:
            moment = event["timestamp"]
    return moment


def _office(location: Any) -> str | None:
    """Format ``<office> | <TOWN>`` as ``<office>, <TOWN>``; ``None`` without an office."""
    office, _, town = str(location or "").partition("|")
    office, town = office.strip(), town.strip()
    if not office:
        return None
    return f"{office}, {town}" if town else office


def _pickup_point(
    events: list[dict], statuses: list[ParcelStatus | None]
) -> str | None:
    """Name the office a Bulgarian parcel was held at, newest event first."""
    for event, status in zip(reversed(events), reversed(statuses), strict=True):
        if status is ParcelStatus.AT_PICKUP_POINT and (
            office := _office(event.get("location"))
        ):
            return office
    return None


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The status is the newest event that maps; a parcel whose tracker has not
    answered yet (``events`` empty) is ``unknown`` without a warning. The
    trackers show no ETA, weight or parties, so those stay ``None``; only the
    Bulgarian tracker names a pickup point.
    """
    events = [e for e in raw.get("events") or [] if isinstance(e, dict)]
    statuses = event_statuses(raw.get("source"), events)
    status = ParcelStatus.UNKNOWN
    raw_status = events[-1].get("wording") if events else None
    for event, mapped in zip(reversed(events), reversed(statuses), strict=True):
        if mapped is not None:
            status = mapped
            raw_status = event.get("wording")
            break
    else:
        if events:
            if "no mappable event" not in _unmapped_statuses_logged:
                _unmapped_statuses_logged.add("no mappable event")
                _LOGGER.warning(
                    "No Express One scan of a parcel sets a status yet, so it"
                    " shows unknown. If it stays that way, open an issue: %s",
                    NEW_ISSUE_URL,
                )
    delivered = status is ParcelStatus.DELIVERED
    barcode = raw.get("barcode")
    pickup = is_pickup(None, status, statuses)
    pickup_point = (
        _pickup_point(events, statuses)
        if pickup and raw.get("source") == "bg"
        else None
    )

    return {
        "carrier": "Express One",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "contents": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": _delivered_at(events, statuses) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": pickup,
        "pickup_point": pickup_point,
        "url": _page_url(raw),
        "weight": None,
        "dimensions": None,
        "history": build_history(events, statuses) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]

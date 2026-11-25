"""Constants for the Express One parcel tracker integration."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "express_one"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"  # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"  # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"  # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"  # Ready to collect at a pickup location
    DELIVERED = "delivered"  # Handed over
    RETURNING = "returning"  # Failed delivery, going back to sender
    PROBLEM = "problem"  # Carrier reports an exception/issue
    UNKNOWN = "unknown"  # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {
        "weight",
        "dimensions",
        "delivery_window",
        "pickup_point",
        "url",
        "history",
        "contents",
    }
)

# Which optional contract fields each backend populates, keyed like the
# country selector's labels; order is display order on the docs site. The
# Croatian entry reads the Hungarian-side tracker, which names no pickup point;
# the Bulgarian entry reads the Bulgarian tracker first, whose office events
# carry one. Every other optional field comes back as a literal ``None``.
CAPABILITIES_BY_VARIANT = {
    "Croatia": frozenset({"url", "history"}),
    "Bulgaria": frozenset({"url", "history", "pickup_point"}),
}

# The tracker that also serves Croatia answers a GET keyed on the code. A
# found parcel is HTTP 200 with the scan history in the page; an unknown,
# not-yet-registered or aged-out code is HTTP 404 with an "unavailable" notice.
TRACKING_API_URL = "https://tracking.expressone.hu/?plc_number={tracking_code}"
TRACKING_URL = TRACKING_API_URL

# The Bulgarian tracker is a form: the session page hands out a cookie and a
# one-time form token, and the lookup POSTs both back with the number.
BG_FORM_URL = "https://expressone.bg/en/tracking"
BG_LOOKUP_URL = "https://expressone.bg/en/tracking/{number}"

# The pages are a few tens of KB; anything past this is not a tracking page.
MAX_BODY_BYTES = 512 * 1024

# The country picked in the config flow. It only decides which tracker a code
# goes to; Slovenia is listed so a Slovenian user finds out why it does not
# work yet, but never becomes an entry.
CONF_COUNTRY = "country"
COUNTRY_CROATIA = "HR"
COUNTRY_BULGARIA = "BG"
COUNTRY_SLOVENIA = "SI"
SUPPORTED_COUNTRIES = (COUNTRY_CROATIA, COUNTRY_BULGARIA)
COUNTRY_TITLES = {
    COUNTRY_CROATIA: "Express One Croatia",
    COUNTRY_BULGARIA: "Express One Bulgaria",
}
SLOVENIA_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-express-one/issues/3"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_CONTENTS = "contents"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight (registered, in_transit, at_pickup_point, unknown, problem,
# returning).
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20

"""Express One tracking client.

Two trackers sit behind one contract:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier says the tracking code is unknown, not
  registered yet or aged out (a normal, expected state — never an error),
* raises :class:`ExpressOneApiError` for anything that cannot be positively
  read, with ``status_code`` set on a non-2xx response and ``retry_after`` set
  when the carrier's own ``Retry-After`` header on a 429 could be parsed as
  seconds,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.

Wall clocks on both pages carry no offset; each is dated in its own tracker's
zone.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import logging
import re
import ssl
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    BG_FORM_URL,
    BG_LOOKUP_URL,
    COUNTRY_BULGARIA,
    MAX_BODY_BYTES,
    TRACKING_API_URL,
)
from .parcels import NEW_ISSUE_URL
from .parser import (
    ExpressOneParseError,
    bg_form_token,
    hu_echoed_number,
    hu_not_found,
    parse_bg_page,
    parse_hu_page,
)

_LOGGER = logging.getLogger(__name__)

HU_ZONE = ZoneInfo("Europe/Budapest")
BG_ZONE = ZoneInfo("Europe/Sofia")

SOURCE_HU = "hu"
SOURCE_BG = "bg"

# One attempt may use half of the budget a refresh is allowed per tracker.
ATTEMPT_TIMEOUT = aiohttp.ClientTimeout(total=10)

_BILL_RE = re.compile(r"^\d{8}$")

# Fixed warning texts already logged this session, so each is shown once.
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    """Log a fixed, value-free warning once per HA session."""
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning("%s Open an issue and paste this line: %s", message, NEW_ISSUE_URL)


class ExpressOneApiError(Exception):
    """Raised when an Express One call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Express One request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def _clock(
    day: str, time_text: str, day_format: str, time_format: str, zone
) -> str | None:
    """Return the aware ISO moment of a wall-clock date and time, or ``None``."""
    try:
        naive = datetime.strptime(f"{day} {time_text}", f"{day_format} {time_format}")
    except ValueError:
        return None
    return naive.replace(tzinfo=zone).isoformat()


def route_sources(country: str, tracking_code: str) -> list[str]:
    """Return the trackers to ask for a code, in order.

    Eight digits are a Bulgarian bill of lading. A Bulgarian entry asks the
    Bulgarian tracker first for every code, because it holds the local times
    and the pickup office of a parcel delivered there; the Hungarian-side
    tracker only covers the leg before that and is asked when the Bulgarian one
    does not know the code. A Croatian entry never reaches the Bulgarian one.
    """
    if country != COUNTRY_BULGARIA:
        return [SOURCE_HU]
    if _BILL_RE.match(tracking_code):
        return [SOURCE_BG]
    return [SOURCE_BG, SOURCE_HU]


class ExpressOneApiClient:
    """Client for the public Express One trackers.

    No authentication: both trackers are keyed on the tracking code alone.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        country: str,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """Initialise the client.

        ``session`` must not keep cookies: the Bulgarian tracker's session
        cookie is read and sent by hand, one session per lookup.
        """
        self._session = session
        self._country = country
        self._ssl = ssl_context

    def _ssl_kwargs(self) -> dict[str, Any]:
        """Return the per-request TLS argument, empty for the default context."""
        return {"ssl": self._ssl} if self._ssl is not None else {}

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details, or ``None`` when unknown."""
        for source in route_sources(self._country, tracking_code):
            if source == SOURCE_HU:
                parcel = await self._async_get_hu(tracking_code)
            else:
                parcel = await self._async_get_bg(tracking_code)
            # Only a "not found" answer is None; an inconclusive one raises
            # and must not fall through to the next tracker.
            if parcel is not None:
                return parcel
        return None

    async def _async_request(
        self, method: str, url: str, **kwargs: Any
    ) -> tuple[int, Mapping[str, str], Any, str]:
        """Send a request; return status, headers, cookies and the capped body."""
        async with self._session.request(
            method,
            url,
            allow_redirects=False,
            timeout=ATTEMPT_TIMEOUT,
            **kwargs,
        ) as response:
            status = response.status
            headers = response.headers
            cookies = response.cookies
            if status == 429:
                return status, headers, cookies, ""
            # read(n) returns whatever has arrived, not the whole body, so
            # collect chunks until the end or the cap.
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    break
        if size > MAX_BODY_BYTES:
            raise ExpressOneApiError("reply too large")
        return status, headers, cookies, b"".join(chunks).decode("utf-8", "replace")

    @staticmethod
    def _check_status(status: int, headers: Mapping[str, str]) -> None:
        """Raise for a throttle answer; other statuses are the caller's call."""
        if status != 429:
            return
        header = headers.get("Retry-After")
        try:
            retry_after = float(header) if header else None
        except ValueError:
            retry_after = None  # an HTTP-date, not seconds
        raise ExpressOneApiError("HTTP 429", status_code=429, retry_after=retry_after)

    @staticmethod
    def _require_html(page: str, who: str) -> None:
        """Raise unless the body looks like an HTML page."""
        head = page[:2000].lower()
        if "<html" not in head and "<!doctype" not in head:
            _warn_once(
                f"{who}-not-html",
                f"Express One answered with something other than a web page ({who}).",
            )
            raise ExpressOneApiError("not an HTML page")

    async def _async_get_hu(self, tracking_code: str) -> dict[str, Any] | None:
        """Look a code up on the Hungarian-side tracker."""
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        headers = {"Accept": "text/html", "Accept-Language": "en"}
        try:
            reply = await self._async_request("GET", url, headers=headers)
        except (aiohttp.ClientError, TimeoutError):
            # One retry for a network failure, nothing else is retried.
            reply = await self._async_request("GET", url, headers=headers)
        status, reply_headers, _, page = reply
        self._check_status(status, reply_headers)

        if status == 404:
            if hu_not_found(page):
                return None
            _warn_once(
                "hu-404",
                "Express One answered 404 without its usual notice (hu).",
            )
            raise ExpressOneApiError("HTTP 404", status_code=404)
        if status != 200:
            raise ExpressOneApiError(f"HTTP {status}", status_code=status)
        self._require_html(page, "hu")

        if hu_echoed_number(page) != tracking_code:
            _warn_once(
                "hu-echo",
                "Express One's page did not echo the number that was asked for (hu).",
            )
            raise ExpressOneApiError("echo mismatch")
        try:
            events, extra_blocks = parse_hu_page(page)
        except ExpressOneParseError as err:
            _warn_once(
                f"hu-parse:{err}",
                f"Express One's tracking page was not recognised (hu: {err}).",
            )
            raise ExpressOneApiError(f"unexpected page ({err})") from err
        if extra_blocks:
            _warn_once(
                "hu-extra-block",
                "Express One's page has a second history block that is not read (hu).",
            )

        rows = self._timestamped(events, "%d-%m-%Y", "%H:%M:%S", HU_ZONE)
        return {
            "source": SOURCE_HU,
            "lookup": tracking_code,
            "barcode": tracking_code,
            "events": rows,
        }

    async def _async_get_bg(self, number: str) -> dict[str, Any] | None:
        """Look a number up on the Bulgarian tracker."""
        status, headers, cookies, page = await self._async_request(
            "GET",
            BG_FORM_URL,
            headers={"Accept": "text/html"},
            **self._ssl_kwargs(),
        )
        self._check_status(status, headers)
        if status != 200:
            raise ExpressOneApiError(f"HTTP {status}", status_code=status)
        self._require_html(page, "bg")
        token = bg_form_token(page)
        session_cookie = cookies.get("PHPSESSID")
        if not token or session_cookie is None:
            _warn_once(
                "bg-form",
                "Express One's tracking form was not recognised (bg).",
            )
            raise ExpressOneApiError("no form token")

        status, headers, _, page = await self._async_request(
            "POST",
            BG_LOOKUP_URL.format(number=number),
            headers={
                "Accept": "text/html",
                "Cookie": f"PHPSESSID={session_cookie.value}",
            },
            data={"form[bols]": number, "form[_token]": token},
            **self._ssl_kwargs(),
        )
        self._check_status(status, headers)
        if status != 200:
            raise ExpressOneApiError(f"HTTP {status}", status_code=status)
        self._require_html(page, "bg")
        try:
            parsed = parse_bg_page(page)
        except ExpressOneParseError as err:
            _warn_once(
                f"bg-parse:{err}",
                f"Express One's tracking page was not recognised (bg: {err}).",
            )
            raise ExpressOneApiError(f"unexpected page ({err})") from err
        if parsed is None:
            return None

        bill, events = parsed
        if _BILL_RE.match(number) and bill != number:
            _warn_once(
                "bg-echo",
                "Express One's page answered for another number than asked (bg).",
            )
            raise ExpressOneApiError("echo mismatch")
        # The page lists newest first and repeats rows; keep each once, oldest
        # first like the other tracker.
        unique: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for event in reversed(events):
            key = (event["date"], event["time"], event["wording"], event["location"])
            if key not in seen:
                seen.add(key)
                unique.append(event)
        rows = self._timestamped(unique, "%d.%m.%Y", "%H:%M", BG_ZONE)
        return {
            "source": SOURCE_BG,
            "lookup": number,
            # The entered code stays the barcode: the source can switch
            # between trackers during a parcel's life.
            "barcode": number,
            "bill_of_lading": bill,
            "events": rows,
        }

    @staticmethod
    def _timestamped(
        events: list[dict[str, Any]], day_format: str, time_format: str, zone
    ) -> list[dict[str, Any]]:
        """Add each event's aware ``timestamp``; unreadable rows are dropped."""
        rows: list[dict[str, Any]] = []
        for event in events:
            timestamp = _clock(
                event["date"], event["time"], day_format, time_format, zone
            )
            if timestamp is None:
                _warn_once(
                    "bad-clock",
                    "Express One returned a scan time that could not be read.",
                )
                continue
            rows.append({**event, "timestamp": timestamp})
        if not rows:
            raise ExpressOneApiError("no readable scan rows")
        return rows

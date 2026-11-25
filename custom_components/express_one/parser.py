"""Readers for the two Express One tracking pages.

Pure functions over the page text: no I/O, no Home Assistant objects. Each
reader fails closed — a page that does not have the expected structure raises
:class:`ExpressOneParseError` instead of yielding a half-read parcel.
"""

from __future__ import annotations

import html
import re
from typing import Any

# --- Hungarian-side tracker ------------------------------------------------

_HU_BLOCK_START = 'class="history"'
_HU_TOKEN_RE = re.compile(
    r'<div class="headline">\s*<h5>\s*<span>(?P<date>\d{2}-\d{2}-\d{4})</span>'
    r"|<tr>\s*<td[^>]*>(?P<time>\d{2}:\d{2}:\d{2})</td>\s*"
    r"<td[^>]*>(?P<code>[A-Z0-9]{3})</td>\s*<td[^>]*>(?P<text>.*?)</td>",
    re.DOTALL,
)
_HU_LOCATION_RE = re.compile(r"\s*(\([A-Z]{2} - [^()]+\))\s*$")
_HU_ECHO_RE = re.compile(r"plc_number=(\d+)&(?:amp;)?setlang=")
_HU_NOTICE_MARKERS = ("currently unavailable", "jelenleg nem elérhető")

# --- Bulgarian tracker -----------------------------------------------------

_BG_TOKEN_TAG_RE = re.compile(r'<input[^>]*name="form\[_token\]"[^>]*>')
_BG_VALUE_RE = re.compile(r'value="([^"]+)"')
_BG_CARD_RE = re.compile(r"<span>\s*Shipment N\s*(\d+)\s*-(.*?)</span>", re.DOTALL)
_BG_ROW_RE = re.compile(r'<tr class="(?!row-separator)[^"]*">(.*?)</tr>', re.DOTALL)
_BG_CELL_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
_BG_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_BG_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_BG_NO_RESULTS = "No results found"

# Wordings that themselves contain the " - " separator, so a split on the
# first one would cut them in half.
_BG_DASHED_WORDINGS = ("Data received - shipment pending",)

_TAG_RE = re.compile(r"<[^>]+>")


class ExpressOneParseError(Exception):
    """Raised when a page does not have the structure the reader expects."""


def _text(fragment: str) -> str:
    """Return a fragment's text: tags dropped, entities decoded, spaces collapsed."""
    return " ".join(html.unescape(_TAG_RE.sub(" ", fragment)).split())


def hu_not_found(page: str) -> bool:
    """Whether a 404 page carries the "package unavailable" notice."""
    return 'class="alert' in page and any(
        marker in page for marker in _HU_NOTICE_MARKERS
    )


def hu_echoed_number(page: str) -> str | None:
    """Return the number the page says it looked up, or ``None``."""
    match = _HU_ECHO_RE.search(page)
    return match.group(1) if match else None


def parse_hu_page(page: str) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(events, extra_blocks)`` from the scan history of a found page.

    Events come in page order: days ascending, rows ascending. ``extra_blocks``
    is true when the page carries more than one history block (a returned
    parcel's second block is not read).
    """
    start = page.find(_HU_BLOCK_START)
    if start < 0:
        raise ExpressOneParseError("no history block")
    second = page.find(_HU_BLOCK_START, start + len(_HU_BLOCK_START))
    block = page[start : second if second >= 0 else len(page)]

    events: list[dict[str, Any]] = []
    day: str | None = None
    for token in _HU_TOKEN_RE.finditer(block):
        if token.group("date"):
            day = token.group("date")
            continue
        if day is None:
            raise ExpressOneParseError("scan row before a date")
        text = _text(token.group("text"))
        location = None
        found = _HU_LOCATION_RE.search(text)
        if found:
            location = found.group(1)
            text = text[: found.start()].strip()
        events.append(
            {
                "date": day,
                "time": token.group("time"),
                "code": token.group("code"),
                "wording": text,
                "location": location,
            }
        )
    if not events:
        raise ExpressOneParseError("no scan rows")
    return events, second >= 0


def bg_form_token(page: str) -> str | None:
    """Return the hidden form token of the Bulgarian tracking page."""
    tag = _BG_TOKEN_TAG_RE.search(page)
    value = _BG_VALUE_RE.search(tag.group(0)) if tag else None
    return html.unescape(value.group(1)) if value else None


def _split_bg_status(text: str) -> tuple[str, str | None]:
    """Split ``<wording> - <office> | <TOWN>`` into wording and location."""
    for wording in _BG_DASHED_WORDINGS:
        if text == wording or text.startswith(f"{wording} - "):
            rest = text[len(wording) :].removeprefix(" - ").strip()
            return wording, rest or None
    wording, separator, rest = text.partition(" - ")
    rest = rest.strip() if separator else ""
    return wording.strip(), rest or None


def parse_bg_page(page: str) -> tuple[str, list[dict[str, Any]]] | None:
    """Return ``(bill_of_lading, events)`` for a lookup page, or ``None``.

    ``None`` means the page says there is no such shipment. Events come in
    page order, newest first, repeated rows still included.
    """
    cards = _BG_CARD_RE.findall(page)
    if not cards:
        if _BG_NO_RESULTS in page:
            return None
        raise ExpressOneParseError("no shipment card")
    if len(cards) != 1:
        raise ExpressOneParseError("more than one shipment")

    events: list[dict[str, Any]] = []
    for row in _BG_ROW_RE.finditer(page[page.find("Shipment N") :]):
        cells = [_text(cell) for cell in _BG_CELL_RE.findall(row.group(1))]
        if (
            len(cells) != 4
            or not _BG_DATE_RE.match(cells[0])
            or not _BG_TIME_RE.match(cells[2])
        ):
            raise ExpressOneParseError("unreadable scan row")
        wording, location = _split_bg_status(cells[3])
        if not wording:
            raise ExpressOneParseError("empty status")
        events.append(
            {
                "date": cells[0],
                "time": cells[2],
                "wording": wording,
                "location": location,
            }
        )
    if not events:
        raise ExpressOneParseError("no scan rows")
    return cards[0][0], events

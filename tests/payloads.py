"""Synthetic Express One payloads shared by the test modules.

Page builders reproduce the shapes of the two trackers with invented numbers,
places and offices; raw samples are what ``api.py`` hands the coordinator.
Keeping them in one module means a changed page shape has exactly one place
to fix.
"""

from __future__ import annotations

ACTIVE_CODE = "158278800000000000001111"
DELIVERED_CODE = "158278800000000000002222"
OTHER_CODE = "461011100000000000003333"
BILL = "29000001"
OTHER_BILL = "29000002"
TOKEN = "tok3n-Abc_123"
SESSION = "sessionid123"

DELIVERED_AT = "2026-04-29T13:12:42+02:00"


def event(code: str, timestamp: str, wording: str, location: str | None = None) -> dict:
    """One entry of the raw event list, in the Hungarian-side shape."""
    return {
        "date": "29-04-2026",
        "time": "13:12:42",
        "code": code,
        "wording": wording,
        "location": location,
        "timestamp": timestamp,
    }


def bg_event(wording: str, timestamp: str, location: str | None = None) -> dict:
    """One entry of the raw event list, in the Bulgarian shape."""
    return {
        "date": "29.04.2026",
        "time": "13:12",
        "wording": wording,
        "location": location,
        "timestamp": timestamp,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel from the Hungarian-side tracker."""
    return {
        "source": "hu",
        "lookup": code,
        "barcode": code,
        "events": [
            event("PRE", "2026-04-27T23:03:58+02:00", "Shipping details received"),
            event("INB", "2026-04-28T15:52:17+02:00", "Arrived at hub", "(HU - Town)"),
            event("OFD", "2026-04-29T08:46:00+02:00", "Out for delivery"),
            event("D00", DELIVERED_AT, "Successful delivery"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel."""
    sample = delivered_sample(code)
    sample["events"] = sample["events"][:3]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a pickup point."""
    sample = active_sample(code)
    sample["events"] = [
        *sample["events"],
        event("S07", "2026-04-29T12:00:00+02:00", "Ready for collection"),
    ]
    return sample


def bg_sample(bill: str = BILL, lookup: str | None = None) -> dict:
    """A delivered parcel from the Bulgarian tracker."""
    return {
        "source": "bg",
        "lookup": lookup or bill,
        "barcode": lookup or bill,
        "bill_of_lading": bill,
        "events": [
            bg_event("Data received - shipment pending", "2026-04-27T02:17:00+03:00"),
            bg_event(
                "Parcel is in ExpressOne office",
                "2026-04-28T08:05:00+03:00",
                "| TOWN",
            ),
            bg_event("Delivered", "2026-04-29T15:39:00+03:00", "Office 1 | TOWN"),
        ],
    }


def bg_pickup_sample(bill: str = BILL) -> dict:
    """A parcel waiting at a pickup office, from the Bulgarian tracker."""
    sample = bg_sample(bill)
    sample["events"] = [
        *sample["events"][:2],
        bg_event(
            "Available for office pickup",
            "2026-04-28T14:48:00+03:00",
            "PUP 7005 | TOWN",
        ),
    ]
    return sample


def hu_page(
    code: str,
    days: list[tuple[str, list[tuple[str, str, str, str | None]]]],
    *,
    echo: str | None = None,
    second_block: bool = False,
) -> str:
    """The Hungarian-side tracker's found page.

    ``days`` is ``[(DD-MM-YYYY, [(time, code, wording, location), ...]), ...]``.
    """

    def block() -> str:
        out = ['<div class="history">']
        for day, rows in days:
            out.append(f'<div class="headline"><h5><span>{day}</span></h5></div>')
            out.append('<table class="table mb-0">')
            for time_text, event_code, wording, location in rows:
                where = f"\n\t\t\t\t\t\t\t\t\t({location})" if location else ""
                out.append(
                    f'<tr>\n<td width="1">{time_text}</td>\n'
                    f'<td width="1">{event_code}</td>\n'
                    f"<td>\n\t\t\t\t\t\t\t\t{wording}{where}\n</td>\n"
                    '<!--td class="text-right"></td-->\n</tr>'
                )
            out.append("</table><br />")
        out.append("</div>")
        return "\n".join(out)

    shown = echo or code
    return (
        "<!DOCTYPE html><html><body>"
        f'<a href="https://tracking.expressone.hu/?plc_number={shown}&setlang=hu">HU</a>'
        '<h5 class="text-orange">Shipment history</h5>'
        + block()
        + (block() if second_block else "")
        + "</body></html>"
    )


HU_DELIVERED_DAYS = [
    ("22-07-2026", [("00:23:00", "PRE", "Shipping details received", None)]),
    (
        "28-07-2026",
        [
            ("14:42:08", "INB", "Arrived at central hub", "HU - Budapest"),
            ("14:43:00", "DLS", "Shipment data uploaded", None),
        ],
    ),
    ("29-07-2026", [("10:56:56", "E82", "Delivery schedule updated", None)]),
    ("30-07-2026", [("14:58:06", "DEP", "Arrived at depot", "HU - Town")]),
    ("31-07-2026", [("14:35:00", "D00", "Successful delivery", None)]),
]

HU_PICKUP_DAYS = [
    ("28-07-2026", [("14:42:08", "INB", "Arrived at central hub", "HU - Budapest")]),
    (
        "29-07-2026",
        [
            ("08:00:00", "OFD", "Out for delivery", None),
            ("11:00:00", "E12", "Delivery attempt failed", None),
        ],
    ),
    ("30-07-2026", [("09:30:00", "S07", "Ready for collection", "HU - Town")]),
]

HU_NOT_FOUND_EN = (
    "<!DOCTYPE html><html><body>"
    '<div class="alert alert-danger" role="alert"><p><b>The requested package is '
    "currently unavailable in our system.</b></p></div></body></html>"
)
HU_NOT_FOUND_HU = (
    "<!DOCTYPE html><html><body>"
    '<div class="alert alert-danger" role="alert"><p><b>A keresett csomag '
    "jelenleg nem elérhető a rendszerünkben.</b></p></div></body></html>"
)


def bg_form_page(token: str = TOKEN) -> str:
    """The Bulgarian tracker's form page."""
    return (
        "<!DOCTYPE html><html><body>"
        '<form name="form" method="post" action="/en/tracking">'
        '<input type="text" id="form_bols" name="form[bols]" required="required" />'
        f'<input type="hidden" id="form__token" name="form[_token]" '
        f'data-controller="csrf-protection" value="{token}" />'
        "</form></body></html>"
    )


def bg_row(day: str, clock: str, status: str, *, linked: bool = False) -> str:
    """One scan row of the Bulgarian result card."""
    if linked and " - " in status:
        wording, _, place = status.partition(" - ")
        status = f'{wording} - <a href="/en/80">{place}</a>'
    return (
        '<tr class=""><td><span></span></td>'
        f'<td><span class="mobile-title">Date</span><p>\n {day}\n </p></td>'
        '<td class="flexible-col"><span class="mobile-title">Day of the week</span>'
        "<p>Monday</p></td>"
        f'<td class="flexible-col"><span class="mobile-title">Time</span><p>{clock}</p></td>'
        '<td class="flexible-col"><span class="mobile-title">Status</span>'
        f"<p>\n {status}\n </p></td></tr>"
        '<tr class="row-separator"><td colspan="5"></td></tr>'
    )


def bg_result_page(bill: str, rows: list[str], *, heading: str = "Delivered") -> str:
    """The Bulgarian tracker's lookup page; ``rows`` newest first."""
    return (
        bg_form_page()
        + '<div class="card-wrapper card-full-wrapper"><div class="accordion-header">'
        f'<a href="#"><span>Shipment N {bill} -\n   {heading}\n </span></a></div>'
        '<table class="flexible-table"><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


BG_NO_RESULTS = (
    bg_form_page()
    + '<p class="text-default" style="padding: 20px 0;">No results found</p>'
)

BG_ROWS_NEWEST_FIRST = [
    bg_row("29.04.2026", "15:39", "Delivered - Office 1 | TOWN"),
    bg_row(
        "28.04.2026",
        "14:48",
        "Confirmed for delivery by PUDO - Office 1 | TOWN",
        linked=True,
    ),
    bg_row("28.04.2026", "14:48", "Available for office pickup - | TOWN"),
    bg_row("28.04.2026", "14:48", "Available for office pickup - | TOWN"),
    bg_row("27.04.2026", "08:05", "Parcel is in ExpressOne office - | TOWN"),
    bg_row("26.04.2026", "02:17", "Data received - shipment pending"),
]

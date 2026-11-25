"""Tests for the Express One tracker client."""

import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.express_one import api as api_module
from custom_components.express_one.api import (
    ExpressOneApiClient,
    ExpressOneApiError,
    route_sources,
)
from custom_components.express_one.const import COUNTRY_BULGARIA, COUNTRY_CROATIA

from . import payloads as p

CODE = p.DELIVERED_CODE
FOUND = p.hu_page(CODE, p.HU_DELIVERED_DAYS)


@pytest.fixture(autouse=True)
def _reset_warnings():
    api_module._warned.clear()


def _response(status=200, body="", headers=None, cookies=None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.cookies = cookies if cookies is not None else {}
    raw = body.encode() if isinstance(body, str) else body

    async def iter_chunked(size):
        # Small slices, like a real stream, so reassembly is exercised.
        for start in range(0, len(raw), 1000):
            yield raw[start : start + 1000]

    response.content.iter_chunked = iter_chunked
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _session(*replies) -> MagicMock:
    """A session answering each request with the next reply (or raising it)."""
    session = MagicMock()
    session.request = MagicMock(side_effect=list(replies))
    return session


def _cookie(value=p.SESSION):
    return {"PHPSESSID": MagicMock(value=value)}


def _bg_replies(result_page):
    return (
        _response(body=p.bg_form_page(), cookies=_cookie()),
        _response(body=result_page),
    )


def _hu(country, *replies):
    session = _session(*replies)
    return session, ExpressOneApiClient(session, country)


@pytest.mark.parametrize(
    ("country", "code", "expected"),
    [
        (COUNTRY_CROATIA, "158278800000000000001111", ["hu"]),
        (COUNTRY_CROATIA, "4610111000000", ["hu"]),
        (COUNTRY_BULGARIA, "158278800000000000001111", ["bg", "hu"]),
        (COUNTRY_BULGARIA, "4610111000000", ["bg", "hu"]),
        (COUNTRY_BULGARIA, "29000001", ["bg"]),
        (COUNTRY_CROATIA, "29000001", ["hu"]),
    ],
)
def test_routing(country, code, expected):
    assert route_sources(country, code) == expected


async def test_hu_found_page_is_read_with_zoned_clocks():
    session, client = _hu(COUNTRY_CROATIA, _response(body=FOUND))

    parcel = await client.async_get_parcel(CODE)

    assert parcel["source"] == "hu"
    assert parcel["barcode"] == parcel["lookup"] == CODE
    assert [e["code"] for e in parcel["events"]][-1] == "D00"
    # July in Budapest is UTC+2.
    assert parcel["events"][-1]["timestamp"] == "2026-07-31T14:35:00+02:00"
    args, kwargs = session.request.call_args
    assert args == ("GET", f"https://tracking.expressone.hu/?plc_number={CODE}")
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Accept-Language"] == "en"
    assert "ssl" not in kwargs


@pytest.mark.parametrize("notice", [p.HU_NOT_FOUND_EN, p.HU_NOT_FOUND_HU])
async def test_hu_404_with_the_notice_is_not_found(notice):
    _, client = _hu(COUNTRY_CROATIA, _response(404, notice))
    assert await client.async_get_parcel(CODE) is None


async def test_hu_404_without_the_notice_is_inconclusive(caplog):
    _, client = _hu(COUNTRY_CROATIA, _response(404, "<html>nginx</html>"))
    with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 404
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


async def test_hu_echo_mismatch_is_inconclusive(caplog):
    page = p.hu_page(CODE, p.HU_DELIVERED_DAYS, echo="999")
    _, client = _hu(COUNTRY_CROATIA, _response(body=page))
    with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError):
        await client.async_get_parcel(CODE)
    assert "did not echo" in caplog.text
    assert CODE not in caplog.text


async def test_hu_non_html_body_warns_once(caplog):
    _, client = _hu(COUNTRY_CROATIA, _response(body="{}"), _response(body="{}"))
    for _ in range(2):
        with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError):
            await client.async_get_parcel(CODE)
    assert caplog.text.count("something other than a web page") == 1


async def test_hu_unrecognised_page_raises_and_warns(caplog):
    _, client = _hu(
        COUNTRY_CROATIA,
        _response(body=f"<html><a href='?plc_number={CODE}&setlang=hu'></html>"),
    )
    with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError):
        await client.async_get_parcel(CODE)
    assert "not recognised" in caplog.text


async def test_hu_second_history_block_warns(caplog):
    page = p.hu_page(CODE, p.HU_PICKUP_DAYS, second_block=True)
    _, client = _hu(COUNTRY_CROATIA, _response(body=page))
    with caplog.at_level(logging.WARNING):
        parcel = await client.async_get_parcel(CODE)
    assert len(parcel["events"]) == 4
    assert "second history block" in caplog.text


async def test_hu_unreadable_clock_drops_the_row_with_a_warning(caplog):
    days = [("31-02-2026", [("10:00:00", "INB", "x", None)]), *p.HU_PICKUP_DAYS]
    _, client = _hu(COUNTRY_CROATIA, _response(body=p.hu_page(CODE, days)))
    with caplog.at_level(logging.WARNING):
        parcel = await client.async_get_parcel(CODE)
    assert len(parcel["events"]) == 4
    assert "scan time that could not be read" in caplog.text


async def test_hu_no_readable_clock_at_all_raises():
    days = [("31-02-2026", [("10:00:00", "INB", "x", None)])]
    _, client = _hu(COUNTRY_CROATIA, _response(body=p.hu_page(CODE, days)))
    with pytest.raises(ExpressOneApiError, match="no readable"):
        await client.async_get_parcel(CODE)


@pytest.mark.parametrize("status", [301, 403, 500])
async def test_hu_other_statuses_are_transport_errors(status):
    _, client = _hu(COUNTRY_CROATIA, _response(status, "<html></html>"))
    with pytest.raises(ExpressOneApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == status


@pytest.mark.parametrize(
    ("header", "expected"), [("30", 30.0), ("Wed, 21 Oct", None), (None, None)]
)
async def test_429_carries_retry_after(header, expected):
    headers = {"Retry-After": header} if header else {}
    _, client = _hu(COUNTRY_CROATIA, _response(429, "", headers))
    with pytest.raises(ExpressOneApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == expected


async def test_body_over_the_cap_is_refused():
    big = "x" * (512 * 1024 + 10)
    _, client = _hu(COUNTRY_CROATIA, _response(body=big))
    with pytest.raises(ExpressOneApiError, match="too large"):
        await client.async_get_parcel(CODE)


async def test_hu_network_failure_is_retried_once():
    session, client = _hu(
        COUNTRY_CROATIA, aiohttp.ClientConnectionError(), _response(body=FOUND)
    )
    parcel = await client.async_get_parcel(CODE)
    assert parcel["barcode"] == CODE
    assert session.request.call_count == 2


async def test_hu_second_network_failure_propagates():
    _, client = _hu(COUNTRY_CROATIA, TimeoutError(), TimeoutError())
    with pytest.raises(TimeoutError):
        await client.async_get_parcel(CODE)


async def test_bulgaria_asks_the_bg_tracker_first_and_keeps_the_entered_code():
    session = _session(*_bg_replies(p.bg_result_page(p.BILL, p.BG_ROWS_NEWEST_FIRST)))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["source"] == "bg"
    assert parcel["lookup"] == CODE
    assert parcel["barcode"] == CODE
    assert parcel["bill_of_lading"] == p.BILL
    assert [c.args[0] for c in session.request.call_args_list] == ["GET", "POST"]


async def test_bulgaria_falls_back_to_hu_only_when_bg_says_not_found():
    session = _session(*_bg_replies(p.BG_NO_RESULTS), _response(body=FOUND))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["source"] == "hu"
    assert parcel["barcode"] == CODE
    assert [c.args[0] for c in session.request.call_args_list] == [
        "GET",
        "POST",
        "GET",
    ]


async def test_bulgaria_does_not_fall_back_on_an_inconclusive_bg_answer():
    session = _session(
        _response(body=p.bg_form_page(), cookies=_cookie()),
        _response(422, "<html></html>"),
        _response(body=FOUND),
    )
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    with pytest.raises(ExpressOneApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 422
    assert session.request.call_count == 2


async def test_bulgaria_does_not_fall_back_on_a_bad_bg_body():
    session = _session(*_bg_replies(p.bg_form_page()), _response(body=FOUND))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    with pytest.raises(ExpressOneApiError):
        await client.async_get_parcel(CODE)
    assert session.request.call_count == 2


async def test_bulgaria_does_not_fall_back_on_a_bg_network_error():
    session = _session(aiohttp.ClientConnectionError(), _response(body=FOUND))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    with pytest.raises(aiohttp.ClientConnectionError):
        await client.async_get_parcel(CODE)
    assert session.request.call_count == 1


async def test_croatia_never_touches_the_bg_tracker():
    session, client = _hu(COUNTRY_CROATIA, _response(404, p.HU_NOT_FOUND_EN))
    assert await client.async_get_parcel(CODE) is None
    assert session.request.call_count == 1
    assert "expressone.bg" not in session.request.call_args.args[1]


async def test_bulgaria_is_none_when_both_trackers_do_not_know():
    session = _session(*_bg_replies(p.BG_NO_RESULTS), _response(404, p.HU_NOT_FOUND_EN))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    assert await client.async_get_parcel(CODE) is None


async def test_bg_lookup_sends_form_cookie_and_context():
    session = _session(*_bg_replies(p.bg_result_page(p.BILL, p.BG_ROWS_NEWEST_FIRST)))
    context = MagicMock()
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA, context)

    parcel = await client.async_get_parcel(p.BILL)

    assert parcel["source"] == "bg"
    assert parcel["barcode"] == p.BILL
    assert parcel["bill_of_lading"] == p.BILL
    get, post = session.request.call_args_list
    assert get.args == ("GET", "https://expressone.bg/en/tracking")
    assert post.args == ("POST", f"https://expressone.bg/en/tracking/{p.BILL}")
    assert post.kwargs["data"] == {"form[bols]": p.BILL, "form[_token]": p.TOKEN}
    assert post.kwargs["headers"]["Cookie"] == f"PHPSESSID={p.SESSION}"
    assert post.kwargs["ssl"] is context
    assert get.kwargs["ssl"] is context


async def test_bg_events_are_oldest_first_deduplicated_and_sofia_clocks():
    session = _session(*_bg_replies(p.bg_result_page(p.BILL, p.BG_ROWS_NEWEST_FIRST)))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)

    parcel = await client.async_get_parcel(p.BILL)

    events = parcel["events"]
    assert len(events) == 5  # the repeated pickup row is kept once
    assert [e["wording"] for e in events] == [
        "Data received - shipment pending",
        "Parcel is in ExpressOne office",
        "Available for office pickup",
        "Confirmed for delivery by PUDO",
        "Delivered",
    ]
    # April in Sofia is UTC+3.
    assert events[0]["timestamp"] == "2026-04-26T02:17:00+03:00"


async def test_bg_no_results_is_not_found():
    session = _session(*_bg_replies(p.BG_NO_RESULTS))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    assert await client.async_get_parcel(p.BILL) is None


async def test_bg_echo_mismatch_is_inconclusive(caplog):
    session = _session(
        *_bg_replies(p.bg_result_page(p.OTHER_BILL, p.BG_ROWS_NEWEST_FIRST))
    )
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError):
        await client.async_get_parcel(p.BILL)
    assert "another number" in caplog.text


async def test_bg_more_than_one_shipment_is_inconclusive():
    page = p.bg_result_page(p.BILL, p.BG_ROWS_NEWEST_FIRST) * 2
    session = _session(*_bg_replies(page))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    with pytest.raises(ExpressOneApiError, match="unexpected page"):
        await client.async_get_parcel(p.BILL)


async def test_bg_unrecognised_result_warns(caplog):
    session = _session(*_bg_replies(p.bg_form_page()))
    client = ExpressOneApiClient(session, COUNTRY_BULGARIA)
    with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError):
        await client.async_get_parcel(p.BILL)
    assert "not recognised" in caplog.text


@pytest.mark.parametrize(
    "form",
    [
        lambda: _response(body=p.bg_form_page()),  # no session cookie
        lambda: _response(body="<html></html>", cookies=_cookie()),  # no token
    ],
)
async def test_bg_form_without_token_or_cookie_raises(form, caplog):
    client = ExpressOneApiClient(_session(form()), COUNTRY_BULGARIA)
    with caplog.at_level(logging.WARNING), pytest.raises(ExpressOneApiError):
        await client.async_get_parcel(p.BILL)
    assert "form was not recognised" in caplog.text


@pytest.mark.parametrize("status", [403, 500])
async def test_bg_form_error_status_raises(status):
    client = ExpressOneApiClient(
        _session(_response(status, "<html></html>")), COUNTRY_BULGARIA
    )
    with pytest.raises(ExpressOneApiError) as err:
        await client.async_get_parcel(p.BILL)
    assert err.value.status_code == status


@pytest.mark.parametrize("status", [422, 302, 500])
async def test_bg_lookup_error_status_raises(status):
    session = _session(
        _response(body=p.bg_form_page(), cookies=_cookie()),
        _response(status, "<html></html>"),
    )
    with pytest.raises(ExpressOneApiError) as err:
        await ExpressOneApiClient(session, COUNTRY_BULGARIA).async_get_parcel(p.BILL)
    assert err.value.status_code == status


async def test_bg_lookup_non_html_raises():
    session = _session(
        _response(body=p.bg_form_page(), cookies=_cookie()), _response(body="{}")
    )
    with pytest.raises(ExpressOneApiError, match="not an HTML"):
        await ExpressOneApiClient(session, COUNTRY_BULGARIA).async_get_parcel(p.BILL)

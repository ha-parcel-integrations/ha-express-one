"""Tests for the tracker page readers."""

import pytest

from custom_components.express_one.parser import (
    ExpressOneParseError,
    bg_form_token,
    hu_echoed_number,
    hu_not_found,
    parse_bg_page,
    parse_hu_page,
)

from . import payloads as p


def test_hu_rows_come_in_page_order_with_the_day_attached():
    events, extra = parse_hu_page(p.hu_page(p.DELIVERED_CODE, p.HU_DELIVERED_DAYS))

    assert extra is False
    assert [e["code"] for e in events] == ["PRE", "INB", "DLS", "E82", "DEP", "D00"]
    assert (events[0]["date"], events[0]["time"]) == ("22-07-2026", "00:23:00")
    assert events[1]["wording"] == "Arrived at central hub"
    assert events[1]["location"] == "(HU - Budapest)"
    assert events[2]["location"] is None


def test_hu_second_history_block_is_flagged_and_not_read():
    events, extra = parse_hu_page(
        p.hu_page(p.DELIVERED_CODE, p.HU_PICKUP_DAYS, second_block=True)
    )
    assert extra is True
    assert len(events) == 4


def test_hu_page_without_history_raises():
    with pytest.raises(ExpressOneParseError, match="no history block"):
        parse_hu_page("<html></html>")


def test_hu_history_without_rows_raises():
    with pytest.raises(ExpressOneParseError, match="no scan rows"):
        parse_hu_page('<html><div class="history"></div></html>')


def test_hu_row_before_a_date_raises():
    page = (
        '<html><div class="history"><table><tr><td>10:00:00</td><td>INB</td>'
        "<td>x</td></tr></table></div></html>"
    )
    with pytest.raises(ExpressOneParseError, match="before a date"):
        parse_hu_page(page)


def test_hu_echoed_number():
    assert hu_echoed_number(p.hu_page("123", p.HU_PICKUP_DAYS)) == "123"
    assert hu_echoed_number("<html></html>") is None
    assert hu_echoed_number("a?plc_number=55&amp;setlang=hu") == "55"


@pytest.mark.parametrize("page", [p.HU_NOT_FOUND_EN, p.HU_NOT_FOUND_HU])
def test_hu_not_found_notice_in_either_language(page):
    assert hu_not_found(page) is True


def test_hu_not_found_needs_the_notice():
    assert hu_not_found("<html>something else</html>") is False
    assert hu_not_found('<div class="alert">Server busy</div>') is False


def test_bg_form_token():
    assert bg_form_token(p.bg_form_page("a&amp;b")) == "a&b"
    assert bg_form_token("<html></html>") is None
    assert bg_form_token('<input name="form[_token]" />') is None


def test_bg_rows_are_newest_first_with_repeats_and_split_wording():
    bill, events = parse_bg_page(p.bg_result_page(p.BILL, p.BG_ROWS_NEWEST_FIRST))

    assert bill == p.BILL
    assert len(events) == 6
    assert events[0] == {
        "date": "29.04.2026",
        "time": "15:39",
        "wording": "Delivered",
        "location": "Office 1 | TOWN",
    }
    assert events[1]["location"] == "Office 1 | TOWN"
    assert events[2]["location"] == "| TOWN"
    assert events[-1]["wording"] == "Data received - shipment pending"
    assert events[-1]["location"] is None


def test_bg_dashed_wording_with_a_location_is_not_cut():
    page = p.bg_result_page(
        p.BILL,
        [p.bg_row("29.04.2026", "10:00", "Data received - shipment pending - X | Y")],
    )
    _, events = parse_bg_page(page)
    assert events[0]["wording"] == "Data received - shipment pending"
    assert events[0]["location"] == "X | Y"


def test_bg_no_results_is_none():
    assert parse_bg_page(p.BG_NO_RESULTS) is None


def test_bg_page_without_card_or_notice_raises():
    with pytest.raises(ExpressOneParseError, match="no shipment card"):
        parse_bg_page(p.bg_form_page())


def test_bg_more_than_one_shipment_raises():
    page = p.bg_result_page(p.BILL, p.BG_ROWS_NEWEST_FIRST) + p.bg_result_page(
        p.OTHER_BILL, p.BG_ROWS_NEWEST_FIRST
    )
    with pytest.raises(ExpressOneParseError, match="more than one"):
        parse_bg_page(page)


def test_bg_unreadable_row_raises():
    page = p.bg_result_page(p.BILL, [p.bg_row("yesterday", "15:39", "Delivered")])
    with pytest.raises(ExpressOneParseError, match="unreadable"):
        parse_bg_page(page)


def test_bg_empty_status_raises():
    page = p.bg_result_page(p.BILL, [p.bg_row("29.04.2026", "15:39", "")])
    with pytest.raises(ExpressOneParseError, match="empty status"):
        parse_bg_page(page)


def test_bg_card_without_rows_raises():
    with pytest.raises(ExpressOneParseError, match="no scan rows"):
        parse_bg_page(p.bg_result_page(p.BILL, []))

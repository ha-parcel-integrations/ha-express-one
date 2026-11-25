"""Tests for the Express One config and options flow."""

import json
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.express_one
from custom_components.express_one.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.express_one.const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    COUNTRY_BULGARIA,
    DOMAIN,
    SLOVENIA_ISSUE_URL,
)


def test_normalize_tracking_code_removes_spaces():
    assert normalize_tracking_code(" 1582 7880 0000 ") == "158278800000"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


@pytest.mark.parametrize(
    ("code", "valid"),
    [
        ("158278800000000000001111", True),
        ("29000001", True),
        ("abc", True),
        ("", False),
    ],
)
def test_valid_tracking_code_accepts_every_non_empty_code(code, valid):
    assert valid_tracking_code(code) is valid


async def test_user_flow_creates_an_entry_per_country(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    options = result["data_schema"].schema[CONF_COUNTRY].config["options"]
    assert options == ["bg", "hr", "si"]
    assert result["data_schema"].schema[CONF_COUNTRY].config["sort"] is True

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "hr"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Express One Croatia"
    assert result["data"] == {CONF_COUNTRY: "HR"}
    assert result["options"][CONF_PARCELS] == []

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "bg"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Express One Bulgaria"


async def test_same_country_twice_is_rejected(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id=f"{DOMAIN}_HR", data={CONF_COUNTRY: "hr"}
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "hr"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_slovenia_aborts_with_the_issue_link(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: "si"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "slovenia_unsupported"
    assert result["description_placeholders"] == {"issue_url": SLOVENIA_ISSUE_URL}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    "path", ["strings.json", "translations/en.json", "translations/nl.json"]
)
def test_slovenia_abort_text_carries_the_issue_placeholder(path):
    root = Path(custom_components.express_one.__file__).parent
    texts = json.loads((root / path).read_text(encoding="utf-8"))
    assert "{issue_url}" in texts["config"]["abort"]["slovenia_unsupported"]
    assert set(texts["selector"]["country"]["options"]) == {"hr", "bg", "si"}


def test_the_issue_link_points_at_this_repo():
    assert SLOVENIA_ISSUE_URL.startswith(
        "https://github.com/ha-parcel-integrations/ha-express-one/issues/"
    )


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_BG",
        data={CONF_COUNTRY: COUNTRY_BULGARIA},
        options={CONF_PARCELS: parcels},
    )


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["158278800000000000001111"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "158278800000000000001111"}
    ]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with spaces are cleaned the way the tracker's form would."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["1582 7880 0000 0000 0000 1111"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "158278800000000000001111"}
    ]


async def test_options_accepts_a_code_of_any_shape(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["abc"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "abc"}]


async def test_options_de_duplicates_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: "158278800000000000001111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"tracking_codes": ["158278800000000000001111", "158278800000000000001111"]},
    )
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "158278800000000000001111"}
    ]


async def test_options_remove_parcel(hass):
    entry = _hub(
        [
            {CONF_TRACKING_CODE: "158278800000000000001111"},
            {CONF_TRACKING_CODE: "158278800000000000002222"},
        ]
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["158278800000000000002222"]}
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"158278800000000000002222"}


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: "158278800000000000001111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5

"""Config flow for the Express One parcel tracker integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    COUNTRY_BULGARIA,
    COUNTRY_CROATIA,
    COUNTRY_SLOVENIA,
    COUNTRY_TITLES,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    SLOVENIA_ISSUE_URL,
)

_LOGGER = logging.getLogger(__name__)


def normalize_tracking_code(value: str) -> str:
    """Return the tracking code with every space removed."""
    return "".join((value or "").split())


def valid_tracking_code(value: str) -> bool:
    """Accept every non-empty code.

    Real tracking-number formats vary too much to gate on a guessed shape; a
    bad code simply comes back "not found" on the next poll.
    """
    return bool(value)


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


def _clean_tracking_codes(values: list[str] | None) -> list[str]:
    """Normalise, drop blanks, and de-duplicate tracking codes."""
    codes: list[str] = []
    for value in values or []:
        code = normalize_tracking_code(value)
        if code and code not in codes:
            codes.append(code)
    return codes


class ExpressOneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the Express One integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ExpressOneOptionsFlowHandler:
        """Return the options flow handler."""
        return ExpressOneOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which country's tracker the parcels will be looked up on.

        Tracking is keyed on the code alone (no account, no postal code), so
        the country is the only question. Slovenia is listed but ends the flow:
        its tracker is not known yet.
        """
        if user_input is not None:
            # Selector values are lower-case translation keys (hassfest);
            # the entry stores the upper-case country code.
            country = user_input[CONF_COUNTRY].upper()
            if country == COUNTRY_SLOVENIA:
                return self.async_abort(
                    reason="slovenia_unsupported",
                    description_placeholders={"issue_url": SLOVENIA_ISSUE_URL},
                )
            await self.async_set_unique_id(f"{DOMAIN}_{country}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=COUNTRY_TITLES[country],
                data={CONF_COUNTRY: country},
                options={
                    CONF_PARCELS: [],
                    CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                    CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                    CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                },
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_COUNTRY, default=COUNTRY_CROATIA.lower()): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                COUNTRY_BULGARIA.lower(),
                                COUNTRY_CROATIA.lower(),
                                COUNTRY_SLOVENIA.lower(),
                            ],
                            translation_key=CONF_COUNTRY,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            sort=True,
                        )
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)


class ExpressOneOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels separately from integration settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer parcel management separately from integration settings."""
        return self.async_show_menu(
            step_id="init", menu_options=["parcels", "settings"]
        )

    async def async_step_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the complete tracked-code list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = _clean_tracking_codes(user_input.get("tracking_codes"))
            if any(not valid_tracking_code(code) for code in codes):
                errors["base"] = "invalid_tracking_code"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
                        CONF_DELIVERED_FILTER_TYPE: self.config_entry.options.get(
                            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                        ),
                        CONF_DELIVERED_FILTER_AMOUNT: self.config_entry.options.get(
                            CONF_DELIVERED_FILTER_AMOUNT,
                            DEFAULT_DELIVERED_FILTER_AMOUNT,
                        ),
                        CONF_INCLUDE_HISTORY: self.config_entry.options.get(
                            CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                        ),
                    },
                )
        current_codes = [
            p[CONF_TRACKING_CODE] for p in _current_parcels(self.config_entry)
        ]
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="parcels",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current_codes}
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the non-parcel integration settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_PARCELS: _current_parcels(self.config_entry),
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                },
            )
        current = self.config_entry.options
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_DELIVERED_FILTER_TYPE,
                default=current.get(
                    CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["days", "parcels"],
                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_DELIVERED_FILTER_AMOUNT,
                default=current.get(
                    CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_INCLUDE_HISTORY,
                default=current.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY),
            ): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))

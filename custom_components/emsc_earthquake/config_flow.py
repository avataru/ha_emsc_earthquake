"""Config flow for the EMSC Earthquake integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_CENTER_LATITUDE,
    CONF_CENTER_LONGITUDE,
    CONF_MIN_MAG,
    CONF_RADIUS_KM,
    CONF_TOTAL_MAX_MAG,
    DEFAULT_NAME,
    DOMAIN,
)


def _filter_fields(defaults: dict[str, Any]) -> dict[vol.Marker, Any]:
    """Return the form fields for the location and magnitude filters."""

    def required(key: str) -> vol.Required:
        default = defaults.get(key)
        if default is None:
            return vol.Required(key)
        return vol.Required(key, default=default)

    return {
        required(CONF_CENTER_LATITUDE): vol.All(
            vol.Coerce(float), vol.Range(min=-90, max=90)
        ),
        required(CONF_CENTER_LONGITUDE): vol.All(
            vol.Coerce(float), vol.Range(min=-180, max=180)
        ),
        required(CONF_RADIUS_KM): vol.All(vol.Coerce(float), vol.Range(min=0)),
        required(CONF_MIN_MAG): vol.Coerce(float),
        required(CONF_TOTAL_MAX_MAG): vol.Coerce(float),
    }


class EMSCEarthquakeFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the EMSC Earthquake integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user for the sensor name, location and filters."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME), data=user_input
            )

        # Pre-fill the centre with the home location configured in Home Assistant.
        defaults = {
            CONF_CENTER_LATITUDE: self.hass.config.latitude,
            CONF_CENTER_LONGITUDE: self.hass.config.longitude,
        }
        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                **_filter_fields(defaults),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this integration."""
        return EMSCEarthquakeOptionsFlowHandler()


class EMSCEarthquakeOptionsFlowHandler(OptionsFlow):
    """Let the user change the filters after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the current filters and save the new ones as options."""
        if user_input is not None:
            # Saving options triggers the update listener in __init__.py,
            # which reloads the integration with the new values.
            return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(_filter_fields(current))
        )

import datetime
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

from .const import CONF_CONDITION, CONF_LIGHTS, CONF_TIMEOUT, DOMAIN
from .helper import convert_to_safejson, timedelta_to_dict

_LOGGER = logging.getLogger(__name__)


def _get_schema(
    default_lights=None,
    default_timeout: dict = None,
    default_condition: dict | None = None,
    with_title: bool = True,
):
    """Return the schema for the form (user and options)."""
    if default_lights is None:
        default_lights = []
    if default_timeout is None:
        # default 5 minutes
        default_timeout = {"hours": 0, "minutes": 5, "seconds": 0}

    schema_title = {
        vol.Required(CONF_NAME, default="Light Timeout"): str,
    }
    schema_options = {
        vol.Required(CONF_LIGHTS, default=default_lights): selector.EntitySelector(
            selector.SelectSelectorConfig(domain="light", multiple=True)
        ),
        vol.Required(CONF_TIMEOUT, default=default_timeout): selector.DurationSelector(
            selector.DurationSelectorConfig(enable_day=False)
        ),
        vol.Optional(
            CONF_CONDITION,
            default=default_condition,
        ): selector.ConditionSelector(),
    }

    if with_title:
        return vol.Schema({**schema_title, **schema_options})

    return vol.Schema(schema_options)


class LightTimeoutConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Light Timeout."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            timeout = (
                int(
                    cv.time_period_dict(
                        user_input.get(CONF_TIMEOUT, None)
                    ).total_seconds()
                )
                or 0
            )

            condition = convert_to_safejson(user_input[CONF_CONDITION])
            logging.error("Original Condition: %s", user_input[CONF_CONDITION])
            logging.error("JSONSafe Condition: %s", condition)

            if not timeout:
                errors["base"] = "timeout_required"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={},
                    options={
                        CONF_LIGHTS: user_input[CONF_LIGHTS],
                        CONF_TIMEOUT: timeout,
                        CONF_CONDITION: condition,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_get_schema(),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LightTimeoutOptionsFlowHandler()


class LightTimeoutOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow to edit lights and timeout."""

    async def async_step_init(self, user_input=None):
        errors = {}

        if user_input is not None:
            timeout = (
                int(
                    cv.time_period_dict(
                        user_input.get(CONF_TIMEOUT, None)
                    ).total_seconds()
                )
                or 0
            )

            condition = convert_to_safejson(user_input[CONF_CONDITION])
            logging.error("Original Condition: %s", user_input[CONF_CONDITION])
            logging.error("JSONSafe Condition: %s", condition)

            return self.async_create_entry(
                title=self.config_entry.title,
                data={
                    CONF_LIGHTS: user_input[CONF_LIGHTS],
                    CONF_TIMEOUT: timeout,
                    CONF_CONDITION: condition,
                },
            )

        current_lights = self.config_entry.options.get(CONF_LIGHTS)
        current_timeout_td = datetime.timedelta(
            seconds=self.config_entry.options.get(CONF_TIMEOUT)
        )
        current_timeout_dict = timedelta_to_dict(current_timeout_td)
        current_condition = convert_to_safejson(
            self.config_entry.options.get(CONF_CONDITION)
        )

        return self.async_show_form(
            step_id="init",
            data_schema=_get_schema(
                default_lights=current_lights,
                default_timeout=current_timeout_dict,
                default_condition=current_condition,
                with_title=False,
            ),
            errors=errors,
        )

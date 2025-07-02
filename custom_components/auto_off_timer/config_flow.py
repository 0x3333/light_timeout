import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME, SERVICE_TURN_OFF
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.selector import NumberSelectorConfig

from .const import CONF_ENABLE_TEMPLATE, CONF_ENTITIES, CONF_TIMEOUT, DOMAIN


def _seconds_to_dict(seconds: int) -> dict:
    """Convert total seconds into a dict compatible with DurationSelector."""
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return {"hours": hours, "minutes": minutes, "seconds": seconds}


def _get_schema(
    default_entities=None,
#    default_timeout: dict = None,
    default_timeout: int = None,
    default_template: str = None,
    with_title: bool = True,
):
    """Return the schema for the form (user and options)."""
    if default_entities is None:
        default_entities = []
    if default_timeout is None:
        default_timeout = {"hours": 0, "minutes": 5, "seconds": 0}
    if default_template is None:
        default_template = ""

    schema_title = {
        vol.Required(CONF_NAME, default="Auto Off Timer"): str,
    }
    schema_options = {
        vol.Required(CONF_ENTITIES, default=default_entities): selector.EntitySelector(
            selector.EntitySelectorConfig(multiple=True)
        ),

        vol.Required(CONF_TIMEOUT, default=default_timeout): selector.NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                unit_of_measurement="min",
                mode="box"  # reines Eingabefeld statt Slider
            )
        ),
        vol.Optional(
            CONF_ENABLE_TEMPLATE, default=default_template
        ): selector.TemplateSelector(selector.TemplateSelectorConfig()),
    }

    if with_title:
        return vol.Schema({**schema_title, **schema_options})

    return vol.Schema(schema_options)


def _process_user_input(hass, user_input: dict) -> tuple[list[str], int, str, dict]:
    """Process and validate user input."""
    errors = {}
    invalid = []
    placeholders = {}

    entities = user_input[CONF_ENTITIES]
    for entity in entities:
        domain = entity.split(".")[0]
        if not hass.services.has_service(domain, SERVICE_TURN_OFF):
            invalid.append(entity)

    # Eingabe als Minuten → in Sekunden umrechnen
    minutes = user_input.get(CONF_TIMEOUT, 0) or 0
    timeout = int(minutes) * 60

    template = user_input.get(CONF_ENABLE_TEMPLATE, "")

    if not entities or len(entities) == 0:
        errors[CONF_ENTITIES] = "entities_required"
    elif invalid:
        errors[CONF_ENTITIES] = "invalid_entities"
        placeholders["entities_list"] = ", ".join(invalid)
    elif not timeout:
        errors[CONF_TIMEOUT] = "timeout_required"

    return entities, timeout, template, errors, placeholders


class AutoOffTimerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Auto Off Timer."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        placeholders = {}

        default_entities = None
        default_timeout = None
        default_template = None

        if user_input is not None:
            entities, timeout, template, errors, placeholders = _process_user_input(
                self.hass, user_input
            )
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={},  # No persistent data in entry.data
                    options={
                        CONF_ENTITIES: entities,
                        CONF_TIMEOUT: timeout,
                        CONF_ENABLE_TEMPLATE: template,
                    },
                )

            default_entities = user_input.get(CONF_ENTITIES)
            default_timeout = user_input.get(CONF_TIMEOUT)
            default_template = user_input.get(CONF_ENABLE_TEMPLATE)

        return self.async_show_form(
            step_id="user",
            data_schema=_get_schema(
                default_entities=default_entities,
                default_timeout=default_timeout,
                default_template=default_template,
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AutoOffTimerOptionsFlowHandler()


class AutoOffTimerOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow to edit entities and timeout."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}
        placeholders = {}

        if user_input is not None:
            entities, timeout, template, errors, placeholders = _process_user_input(
                self.hass, user_input
            )
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_ENTITIES: entities,
                        CONF_TIMEOUT: timeout,
                        CONF_ENABLE_TEMPLATE: template,
                    },
                )

            default_entities = user_input.get(CONF_ENTITIES)
            default_timeout = user_input.get(CONF_TIMEOUT)
            default_template = user_input.get(CONF_ENABLE_TEMPLATE, "")
        else:
            default_entities = self.config_entry.options.get(CONF_ENTITIES, [])
            # Sekunden → Minuten
            stored_sec = self.config_entry.options.get(CONF_TIMEOUT, 300)
            default_timeout = stored_sec // 60

            default_template = self.config_entry.options.get(CONF_ENABLE_TEMPLATE, "")

        return self.async_show_form(
            step_id="init",
            data_schema=_get_schema(
                with_title=False,
                default_entities=default_entities,
                default_timeout=default_timeout,
                default_template=default_template,
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

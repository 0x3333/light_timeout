import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.template import Template

from .const import CONF_ENABLE_TEMPLATE, CONF_EXPIRY, CONF_LIGHTS, CONF_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Called when options are updated; reloads the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup(hass: HomeAssistant, config: dict):
    """Initialize the integration data store."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """
    Called when the user creates/activates a config entry via the UI (config_flow).
    """

    def remove_expiry_map_entry(entity_id: str):
        entry_data["expiry_map"].pop(entity_id, None)

    def update_expiry_map_entry(entity_id: str, expiry_iso: str):
        entry_data["expiry_map"][entity_id] = expiry_iso

    def save_expiry_map():
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_EXPIRY: entry_data["expiry_map"]}
        )

    async def on_timeout(entity_id: str):
        _LOGGER.debug("Timeout expired for %s; turning off...", entity_id)
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": entity_id}, blocking=True
        )
        entry_data["timers"].pop(entity_id, None)
        remove_expiry_map_entry(entity_id)
        save_expiry_map()

    lights = entry.options.get(CONF_LIGHTS)

    entry_data = hass.data[DOMAIN][entry.entry_id] = {
        "unsubs": {},
        "timers": {},
        "expiry_map": entry.data.get(CONF_EXPIRY, {}).copy(),
    }

    now = datetime.now(timezone.utc)
    for entity_id, expiry_iso in entry_data["expiry_map"].items():
        try:
            expiry = datetime.fromisoformat(expiry_iso)
            # We always create a timer, even if it is expired to ensure the state is consistent
            delay = max((expiry - now).total_seconds(), 1)
            entry_data["timers"][entity_id] = async_call_later(
                hass, delay, lambda _: hass.create_task(on_timeout(entity_id))
            )
            _LOGGER.debug(
                "Restoring Timer scheduled for %s in %s seconds", entity_id, delay
            )
        except Exception:
            remove_expiry_map_entry(entity_id)
    save_expiry_map()

    # Register update listener to reload entry when options change
    entry_data["reload_unsub"] = entry.add_update_listener(_update_listener)

    def _schedule_timeout(entity_id: str):
        """Schedule or renew the off-timer for a light."""
        if entity_id in entry_data["timers"]:
            handle = entry_data["timers"].pop(entity_id)
            handle()

        timeout_seconds = entry.options.get(CONF_TIMEOUT)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        update_expiry_map_entry(entity_id, expiry.isoformat())
        save_expiry_map()

        handle = async_call_later(
            hass, timeout_seconds, lambda _: hass.create_task(on_timeout(entity_id))
        )
        entry_data["timers"][entity_id] = handle
        _LOGGER.debug(
            "Timer scheduled for %s in %s seconds", entity_id, timeout_seconds
        )

    def _cancel_timeout(entity_id: str):
        """Cancel the off-timer for a light."""
        if entity_id in entry_data["timers"]:
            handle = entry_data["timers"].pop(entity_id)
            handle()
            remove_expiry_map_entry(entity_id)
            save_expiry_map()

            _LOGGER.debug("Timer canceled for %s", entity_id)

    @callback
    def _state_change_handler(event) -> None:
        """
        Handle state change events for lights:
        - If new state is ON: schedule/renew timeout.
        - If old state was ON and new state is OFF: cancel timeout.
        """
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if new_state.state == STATE_ON:
            if template := entry.options.get(CONF_ENABLE_TEMPLATE):
                rendered_template = Template(template, hass).async_render()
                if str(rendered_template).strip().lower() not in ("1", "true", "yes", "on"):
                    _LOGGER.warning(
                        "Light %s turned ON, but condition not met", entity_id
                    )
                    return

            _LOGGER.debug("Light %s turned ON → scheduling timeout", entity_id)
            _schedule_timeout(entity_id)
        else:
            if old_state is not None and old_state.state == STATE_ON:
                _LOGGER.debug("Light %s turned OFF → canceling timeout", entity_id)
                _cancel_timeout(entity_id)

    for light_entity in lights:
        unsub = async_track_state_change_event(
            hass, light_entity, _state_change_handler
        )
        entry_data["unsubs"][light_entity] = unsub

    timeout_seconds = entry.options.get(CONF_TIMEOUT)
    _LOGGER.debug(
        "Light Timeout: entry %s configured for %s with timeout %s",
        entry.entry_id,
        lights,
        timeout_seconds,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry, removing listeners and timers."""
    if entry.entry_id not in hass.data[DOMAIN]:
        return True

    entry_data = hass.data[DOMAIN].pop(entry.entry_id)

    for unsub in entry_data["unsubs"].values():
        unsub()

    for handle in entry_data["timers"].values():
        handle()

    if reload_unsub := entry_data.get("reload_unsub"):
        reload_unsub()

    _LOGGER.debug("Light Timeout: entry %s unloaded", entry.entry_id)
    return True

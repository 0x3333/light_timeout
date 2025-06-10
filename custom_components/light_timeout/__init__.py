import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import STATE_ON
from homeassistant.helpers.event import async_track_state_change_event, async_call_later

from .const import DOMAIN, CONF_LIGHTS, CONF_TIMEOUT

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
    lights = entry.options.get(CONF_LIGHTS)

    entry_data = {
        "unsubs": {},
        "timers": {},
    }
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Register update listener to reload entry when options change
    entry_data["reload_unsub"] = entry.add_update_listener(_update_listener)

    def _schedule_timeout(entity_id: str):
        """Schedule or renew the off-timer for a light."""
        if entity_id in entry_data["timers"]:
            handle = entry_data["timers"].pop(entity_id)
            handle()

        async def _on_timeout(now):
            _LOGGER.debug("Timeout expired for %s; turning off...", entity_id)
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": entity_id}, blocking=True
            )
            entry_data["timers"].pop(entity_id, None)

        timeout_seconds = entry.options.get(CONF_TIMEOUT)
        handle = async_call_later(hass, timeout_seconds, _on_timeout)
        entry_data["timers"][entity_id] = handle
        _LOGGER.debug(
            "Timer scheduled for %s in %s seconds", entity_id, timeout_seconds
        )

    def _cancel_timeout(entity_id: str):
        """Cancel the off-timer for a light."""
        if entity_id in entry_data["timers"]:
            handle = entry_data["timers"].pop(entity_id)
            handle()
            _LOGGER.debug("Timer canceled for %s", entity_id)

    @callback
    async def _state_change_handler(event) -> None:
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

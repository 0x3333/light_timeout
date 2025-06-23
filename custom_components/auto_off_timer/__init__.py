import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_TURN_OFF, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template

from .const import (
    CONF_ENABLE_TEMPLATE,
    CONF_ENTITIES,
    CONF_TIMEOUT,
    DOMAIN,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Called when options are updated; reloads the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup(hass: HomeAssistant, config: dict):
    """Initialize the integration data store."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Called when the user creates/activates a config entry via the UI (config_flow)."""

    _LOGGER.debug("Setting up Auto Off Timer for entry %s", entry.entry_id)

    # Initialize store for persistent data
    store = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY_TEMPLATE.format(entry_id=entry.entry_id),
    )

    # Load existing expiry data
    store_data = await store.async_load() or {}
    expiry_map = store_data.get("expiry_timestamps", {})

    async def save_expiry_map():
        """Save expiry map to store."""
        await store.async_save({"expiry_timestamps": entry_data["expiry_map"]})

    def remove_expiry_map_entry(entity_id: str):
        """Remove entity from expiry map."""
        entry_data["expiry_map"].pop(entity_id, None)

    def update_expiry_map_entry(entity_id: str, expiry_iso: str):
        """Update entity expiry timestamp."""
        entry_data["expiry_map"][entity_id] = expiry_iso

    async def on_timeout(entity_id: str):
        """Handle timeout expiration."""
        domain = entity_id.split(".")[0]
        _LOGGER.debug("Timeout expired for %s; calling %s.turn_off", entity_id, domain)

        await hass.services.async_call(
            domain, SERVICE_TURN_OFF, {"entity_id": entity_id}, blocking=True
        )

        entry_data["timers"].pop(entity_id, None)
        remove_expiry_map_entry(entity_id)
        await save_expiry_map()

    entities = entry.options.get(CONF_ENTITIES, [])

    entry_data = hass.data[DOMAIN][entry.entry_id] = {
        "unsubs": {},
        "timers": {},
        "expiry_map": expiry_map.copy(),
        "store": store,
    }

    # Restore existing timers from stored data
    now = datetime.now(timezone.utc)
    for entity_id, expiry_iso in entry_data["expiry_map"].items():
        try:
            expiry = datetime.fromisoformat(expiry_iso)
            # Create timer even if expired to ensure state consistency
            delay = max((expiry - now).total_seconds(), 1)
            entry_data["timers"][entity_id] = async_call_later(
                hass, delay, lambda _, eid=entity_id: hass.create_task(on_timeout(eid))
            )
            _LOGGER.debug("Restoring timer for %s in %s seconds", entity_id, delay)
        except Exception as exc:
            _LOGGER.warning("Failed to restore timer for %s: %s", entity_id, exc)
            remove_expiry_map_entry(entity_id)

    # Save cleaned expiry map
    await save_expiry_map()

    # Register update listener
    entry_data["reload_unsub"] = entry.add_update_listener(_update_listener)

    def _schedule_timeout(entity_id: str):
        """Schedule or renew the off-timer for an entity."""
        # Cancel existing timer
        if entity_id in entry_data["timers"]:
            handle = entry_data["timers"].pop(entity_id)
            handle()

        timeout_seconds = entry.options.get(CONF_TIMEOUT)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

        update_expiry_map_entry(entity_id, expiry.isoformat())
        hass.create_task(save_expiry_map())

        handle = async_call_later(
            hass, timeout_seconds, lambda _: hass.create_task(on_timeout(entity_id))
        )
        entry_data["timers"][entity_id] = handle

        _LOGGER.debug(
            "Timer scheduled for %s in %s seconds", entity_id, timeout_seconds
        )

    def _cancel_timeout(entity_id: str):
        """Cancel the off-timer for an entity."""
        if entity_id in entry_data["timers"]:
            handle = entry_data["timers"].pop(entity_id)
            handle()
            remove_expiry_map_entry(entity_id)
            hass.create_task(save_expiry_map())
            _LOGGER.debug("Timer canceled for %s", entity_id)

    @callback
    def _state_change_handler(event) -> None:
        """Handle state change events for entities."""
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        _LOGGER.debug("State change for %s:", entity_id)
        _LOGGER.debug("Old State: %s", old_state)
        _LOGGER.debug("New State: %s", new_state)

        if new_state is None:
            return

        if new_state.state == STATE_ON:
            # Check enable template if configured
            if template := entry.options.get(CONF_ENABLE_TEMPLATE):
                rendered_template = Template(template, hass).async_render()
                if str(rendered_template).strip().lower() not in (
                    "1",
                    "true",
                    "yes",
                    "on",
                ):
                    _LOGGER.warning(
                        "Entity %s turned ON, but condition not met", entity_id
                    )
                    return

            _schedule_timeout(entity_id)
        elif new_state.state == STATE_OFF:
            _cancel_timeout(entity_id)

    # Track state changes for configured entities
    for entity in entities:
        unsub = async_track_state_change_event(hass, entity, _state_change_handler)
        entry_data["unsubs"][entity] = unsub

    timeout_seconds = entry.options.get(CONF_TIMEOUT)
    _LOGGER.debug(
        "Auto Off Timer: entry %s configured for %s with timeout %s",
        entry.entry_id,
        entities,
        timeout_seconds,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry, removing listeners and timers."""
    if entry.entry_id not in hass.data[DOMAIN]:
        return True

    entry_data = hass.data[DOMAIN].pop(entry.entry_id)

    # Unsubscribe from state changes
    for unsub in entry_data["unsubs"].values():
        unsub()

    # Cancel all active timers
    for handle in entry_data["timers"].values():
        handle()

    # Remove update listener
    if reload_unsub := entry_data.get("reload_unsub"):
        reload_unsub()

    # Clean up store data
    if store := entry_data.get("store"):
        await store.async_remove()
        _LOGGER.debug("Store data removed for entry %s", entry.entry_id)

    _LOGGER.debug("Auto Off Timer: entry %s unloaded", entry.entry_id)
    return True

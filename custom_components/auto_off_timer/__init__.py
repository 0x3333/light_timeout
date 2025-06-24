import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_TURN_OFF, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
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


class AutoOffTimerManager:
    """Manages auto-off timers for entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.store = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_TEMPLATE.format(entry_id=entry.entry_id),
        )
        self.unsubs = {}
        self.timers = {}
        self.expiry_map = {}
        self.reload_unsub = None

    async def async_setup(self) -> bool:
        """Set up the timer manager."""

        # Load existing expiry data
        store_data = await self.store.async_load() or {}
        self.expiry_map = store_data.get("expiry_timestamps", {}).copy()

        # Restore existing timers
        await self._restore_timers()

        # Register update listener
        self.reload_unsub = self.entry.add_update_listener(_update_listener)

        # Track state changes for configured entities
        entities = self.entry.options.get(CONF_ENTITIES, [])
        for entity in entities:
            self.unsubs[entity] = async_track_state_change_event(
                self.hass, entity, self._state_change_handler
            )

        _LOGGER.info(
            "Auto Off Timer: entry %s configured for %s with timeout %s",
            self.entry.entry_id,
            entities,
            self.entry.options.get(CONF_TIMEOUT),
        )
        return True

    async def async_unload(self) -> bool:
        """Unload the timer manager."""

        # Unsubscribe from state changes
        for unsub in self.unsubs.values():
            unsub()
        self.unsubs.clear()

        # Cancel all active timers
        for handle in self.timers.values():
            handle()
        self.timers.clear()

        # Remove update listener
        if self.reload_unsub:
            self.reload_unsub()

        # Clean up store data
        await self.store.async_remove()

        return True

    async def _restore_timers(self) -> None:
        """Restore existing timers from stored data."""

        now = datetime.now(timezone.utc)

        for entity_id, expiry_iso in self.expiry_map.items():
            try:
                expiry = datetime.fromisoformat(expiry_iso)
                # Create timer even if expired to ensure state consistency
                delay = max((expiry - now).total_seconds(), 1)
                self.timers[entity_id] = async_call_later(
                    self.hass,
                    delay,
                    lambda _, eid=entity_id: self.hass.create_task(
                        self._on_timeout(eid)
                    ),
                )
                _LOGGER.info("Restoring timer for %s in %s seconds", entity_id, delay)
            except Exception as exc:
                _LOGGER.error("Failed to restore timer for %s: %s", entity_id, exc)
                self._remove_expiry_map_entry(entity_id)

        # Save cleaned expiry map
        await self._save_expiry_map()

    async def _save_expiry_map(self) -> None:
        """Save expiry map to store."""

        await self.store.async_save({"expiry_timestamps": self.expiry_map})

    def _remove_expiry_map_entry(self, entity_id: str) -> None:
        """Remove entity from expiry map."""

        self.expiry_map.pop(entity_id, None)

    def _update_expiry_map_entry(self, entity_id: str, expiry_iso: str) -> None:
        """Update entity expiry timestamp."""

        self.expiry_map[entity_id] = expiry_iso

    async def _on_timeout(self, entity_id: str) -> None:
        """Handle timeout expiration."""

        domain = entity_id.split(".")[0]
        _LOGGER.debug("Timeout expired for %s; calling %s.turn_off", entity_id, domain)

        await self.hass.services.async_call(
            domain, SERVICE_TURN_OFF, {"entity_id": entity_id}, blocking=True
        )

        self.timers.pop(entity_id, None)
        self._remove_expiry_map_entry(entity_id)
        await self._save_expiry_map()

    def _schedule_timeout(self, entity_id: str) -> None:
        """Schedule or renew the off-timer for an entity."""

        # Cancel existing timer
        if entity_id in self.timers:
            handle = self.timers.pop(entity_id)
            handle()

        timeout_seconds = self.entry.options.get(CONF_TIMEOUT)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

        self._update_expiry_map_entry(entity_id, expiry.isoformat())
        self.hass.create_task(self._save_expiry_map())

        handle = async_call_later(
            self.hass,
            timeout_seconds,
            lambda _: self.hass.create_task(self._on_timeout(entity_id)),
        )
        self.timers[entity_id] = handle

        _LOGGER.debug(
            "Timer scheduled for %s in %s seconds", entity_id, timeout_seconds
        )

    def _cancel_timeout(self, entity_id: str) -> None:
        """Cancel the off-timer for an entity."""

        if entity_id in self.timers:
            handle = self.timers.pop(entity_id)
            handle()
            self._remove_expiry_map_entry(entity_id)
            self.hass.create_task(self._save_expiry_map())
            _LOGGER.debug("Timer canceled for %s", entity_id)

    def _should_enable_for_entity(self, entity_id: str) -> bool:
        """Check if timer should be enabled for entity based on template."""

        template = self.entry.options.get(CONF_ENABLE_TEMPLATE)
        if not template:
            return True

        try:
            rendered_template = Template(template, self.hass).async_render()
            return str(rendered_template).strip().lower() in ("1", "true", "yes", "on")
        except Exception as exc:
            _LOGGER.error("Template evaluation failed for %s: %s", entity_id, exc)
            return False

    @callback
    def _state_change_handler(self, event) -> None:
        """Handle state change events for entities."""

        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if new_state is None:
            return

        _LOGGER.debug(
            "State change for %s, was %s now %s",
            entity_id,
            old_state.state.upper() if old_state else "None",
            new_state.state.upper(),
        )

        if new_state.state == STATE_ON:
            if old_state.state == STATE_UNAVAILABLE:
                _LOGGER.warning(
                    "Entity %s turned ON, but last state was UNAVAILABLE, ignoring",
                    entity_id,
                )
                return
            elif not self._should_enable_for_entity(entity_id):
                _LOGGER.info("Entity %s turned ON, but condition not met", entity_id)
                return
            self._schedule_timeout(entity_id)
        elif new_state.state == STATE_OFF:
            self._cancel_timeout(entity_id)


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Called when options are updated; reloads the config entry."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup(hass: HomeAssistant, config: dict):
    """Initialize the integration data store."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Called when the user creates/activates a config entry via the UI (config_flow)."""

    _LOGGER.info("Setting up Auto Off Timer for entry %s", entry.entry_id)

    manager = AutoOffTimerManager(hass, entry)
    success = await manager.async_setup()

    if success:
        hass.data[DOMAIN][entry.entry_id] = {"manager": manager}

    return success


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry, removing listeners and timers."""

    if entry.entry_id not in hass.data[DOMAIN]:
        return True

    entry_data = hass.data[DOMAIN].pop(entry.entry_id)
    manager = entry_data.get("manager")

    if manager:
        success = await manager.async_unload()
    else:
        success = True

    _LOGGER.info("Auto Off Timer: entry %s unloaded", entry.entry_id)
    return success

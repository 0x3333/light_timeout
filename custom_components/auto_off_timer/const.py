"""Constants used by the Auto Off Timer integration."""

DOMAIN = "auto_off_timer"

# Storage
STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = f"{DOMAIN}_{{entry_id}}"

# Options
CONF_ENTITIES = "entities"
CONF_TIMEOUT = "timeout"
CONF_ENABLE_TEMPLATE = "enable_template"

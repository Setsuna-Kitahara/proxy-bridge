"""Diagnostics support for Proxy Bridge (passwords are never exposed)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import BOOTSTRAP_DOMAIN, DOMAIN, VERSION, effective_options
from .proxy_core import CONF_PASSWORD


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[Any]
) -> dict[str, Any]:
    """Return the current routing state of one entry."""
    options = dict(effective_options(entry))
    password = options.pop(CONF_PASSWORD, None)
    runtime = entry.runtime_data

    return {
        "integration": DOMAIN,
        "version": VERSION,
        "entry": {
            "title": entry.title,
            "entry_id": entry.entry_id,
            "source": entry.source,
            "version": entry.version,
            "options": options,
            "password_set": password not in (None, ""),
        },
        "runtime": runtime.info() if runtime is not None else {"started": False},
        "bootstrap_entries": [
            {"title": other.title, "entry_id": other.entry_id}
            for other in hass.config_entries.async_entries(BOOTSTRAP_DOMAIN)
        ],
    }

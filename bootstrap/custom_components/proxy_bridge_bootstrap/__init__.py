"""Proxy Bridge (bootstrap): the egg you install before the chicken.

Most users should install the managed ``proxy_bridge`` integration through
HACS.  This copy exists for the situation where HACS itself cannot reach
GitHub yet: download the bootstrap archive by other means, copy
``custom_components/proxy_bridge_bootstrap`` into your configuration
directory, restart, and configure your proxy.  Once GitHub is reachable,
install the managed integration; its config flow imports these settings.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .const import DOMAIN, VERSION, effective_options
from .proxy_core import ProxyRuntime, build_bridge_config

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Start the loopback bridge for one bootstrap entry."""
    options = effective_options(entry)
    if not options.get("enabled", True):
        _LOGGER.info("%s: entry %s is disabled", DOMAIN, entry.title)
        entry.runtime_data = None
        return True

    config, errors = build_bridge_config(options)
    if errors or config is None:
        raise ConfigEntryError(f"Invalid proxy settings: {errors}")

    runtime = ProxyRuntime(config, entry.entry_id)
    try:
        await runtime.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(f"Could not start the local bridge: {err}") from err

    entry.runtime_data = runtime
    _LOGGER.info(
        "%s %s active: routing %d host pattern(s) through %s (bridge %s)",
        DOMAIN,
        VERSION,
        len(config.hosts),
        config.proxy.redacted(),
        runtime.bridge.url,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Stop the bridge and drop the routing rule."""
    runtime: ProxyRuntime | None = entry.runtime_data
    entry.runtime_data = None
    if runtime is not None:
        await runtime.async_stop()
    return True

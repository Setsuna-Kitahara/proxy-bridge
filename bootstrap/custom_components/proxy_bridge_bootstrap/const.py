"""Constants for the Proxy Bridge bootstrap integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

DOMAIN = "proxy_bridge_bootstrap"
NAME = "Proxy Bridge (Bootstrap)"
VERSION = "1.0.1"

#: Integration that takes over once HACS works and can install it.
MANAGED_DOMAIN = "proxy_bridge"

MIN_HA_VERSION = "2024.12.0"


def effective_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return the settings in force for ``entry`` (options override data)."""
    merged: dict[str, Any] = dict(entry.data)
    merged.update(entry.options)
    return merged

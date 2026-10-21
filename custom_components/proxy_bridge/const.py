"""Constants for the Proxy Bridge integration.

The transport layer (``proxy_core``) keeps the real configuration keys so the
bootstrap integration can share it verbatim; this module only re-exports them
and adds Home Assistant specific constants.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .proxy_core import (
    CONF_ENABLED,
    CONF_HOST,
    CONF_HOSTS,
    CONF_INSECURE,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROXY_TYPE,
    CONF_RETRIES,
    CONF_TEST_CONNECTION,
    CONF_USERNAME,
    DEFAULT_HOST,
    DEFAULT_HOSTS,
    DEFAULT_PORT,
    DEFAULT_PROXY_TYPE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    HOST_PRESETS,
    PROXY_TYPES,
)

DOMAIN = "proxy_bridge"
NAME = "Proxy Bridge"
VERSION = "1.0.1"

# Integration that users install first when they cannot reach GitHub at all.
BOOTSTRAP_DOMAIN = "proxy_bridge_bootstrap"
BOOTSTRAP_ISSUE_ID = "bootstrap_copy_detected"

SERVICE_PROBE = "probe"

#: Minimum Home Assistant version this integration is written against.
MIN_HA_VERSION = "2024.12.0"

#: Marker for "I have been created from the bootstrap copy".
CONF_IMPORTED_FROM = "imported_from"

__all__ = [
    "BOOTSTRAP_DOMAIN",
    "BOOTSTRAP_ISSUE_ID",
    "CONF_ENABLED",
    "CONF_HOST",
    "CONF_HOSTS",
    "CONF_IMPORTED_FROM",
    "CONF_INSECURE",
    "CONF_NAME",
    "CONF_PASSWORD",
    "CONF_PORT",
    "CONF_PROXY_TYPE",
    "CONF_RETRIES",
    "CONF_TEST_CONNECTION",
    "CONF_USERNAME",
    "DEFAULT_HOST",
    "DEFAULT_HOSTS",
    "DEFAULT_PORT",
    "DEFAULT_PROXY_TYPE",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "DOMAIN",
    "HOST_PRESETS",
    "MIN_HA_VERSION",
    "NAME",
    "PROXY_TYPES",
    "SERVICE_PROBE",
    "VERSION",
    "effective_options",
]


def effective_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return the settings currently in force for ``entry``.

    New entries carry their settings in ``data``; edits made through the
    options flow are written to ``options`` and take precedence.  Merging means
    an old entry keeps working even if a migration only rewrites one of them.
    """
    merged: dict[str, Any] = dict(entry.data)
    merged.update(entry.options)
    return merged

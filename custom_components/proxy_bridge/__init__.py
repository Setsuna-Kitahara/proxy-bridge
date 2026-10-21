"""Proxy Bridge: route selected hosts through an upstream proxy.

The heavy lifting lives in :mod:`proxy_core`, which is shared byte-for-byte
with the bootstrap integration.  This module wires it into Home Assistant:
YAML import, config entries, the ``probe`` service and a repair issue that
nudges users to remove the bootstrap copy once the managed one is installed.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    SOURCE_IMPORT,
    ConfigEntry,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    BOOTSTRAP_DOMAIN,
    BOOTSTRAP_ISSUE_ID,
    CONF_ENABLED,
    CONF_HOSTS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_RETRIES,
    DEFAULT_HOSTS,
    DEFAULT_RETRIES,
    DOMAIN,
    SERVICE_PROBE,
    VERSION,
    effective_options,
)
from .proxy_core import (
    ProxyError,
    ProxyRuntime,
    async_probe,
    build_bridge_config,
    parse_proxy_string,
    proxy_spec_from_options,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = []

#: Persisted across reloads: service registrations and the entry dispatcher.
DATA_SERVICES = "services"
DATA_DISPATCHER = "entry_dispatcher"

YAML_PROXY_ITEM = vol.Schema(
    {
        vol.Required("proxy"): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_HOSTS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_RETRIES, default=DEFAULT_RETRIES): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=10)
        ),
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.All(cv.ensure_list, [YAML_PROXY_ITEM])}, extra=vol.ALLOW_EXTRA
)

PROBE_SCHEMA = vol.Schema(
    {
        vol.Required("proxy"): cv.string,
        vol.Optional(CONF_HOSTS, default=list(DEFAULT_HOSTS)): vol.All(
            cv.ensure_list, [cv.string]
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import ``proxy_bridge:`` YAML blocks as config entries."""
    if DOMAIN not in config:
        return True

    for item in config[DOMAIN]:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(item)
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Start the loopback bridge for one config entry."""
    _async_register_services(hass)
    _async_register_entry_listener(hass)

    options = effective_options(entry)
    if not options.get(CONF_ENABLED, True):
        _LOGGER.info(
            "%s: entry %s is disabled, no traffic is being routed",
            DOMAIN,
            entry.title,
        )
        entry.runtime_data = None
        _async_sync_bootstrap_issue(hass)
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
    _async_sync_bootstrap_issue(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Stop the bridge and drop the routing rule."""
    runtime: ProxyRuntime | None = entry.runtime_data
    entry.runtime_data = None
    if runtime is not None:
        await runtime.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Clean up the repair issue when the last entry goes away."""
    _async_sync_bootstrap_issue(hass)


@callback
def _async_register_entry_listener(hass: HomeAssistant) -> None:
    """React to bootstrap entries being added or removed."""
    store: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if DATA_DISPATCHER in store:
        return

    async def _handle_entry_change(_change: Any, entry: ConfigEntry[Any]) -> None:
        if entry.domain in (DOMAIN, BOOTSTRAP_DOMAIN):
            _async_sync_bootstrap_issue(hass)

    store[DATA_DISPATCHER] = async_dispatcher_connect(
        hass, SIGNAL_CONFIG_ENTRY_CHANGED, _handle_entry_change
    )


@callback
def _async_sync_bootstrap_issue(hass: HomeAssistant) -> None:
    """Warn while a bootstrap copy is installed next to the managed one."""
    entries = hass.config_entries.async_entries(BOOTSTRAP_DOMAIN)
    if not entries:
        async_delete_issue(hass, DOMAIN, BOOTSTRAP_ISSUE_ID)
        return

    async_create_issue(
        hass,
        DOMAIN,
        BOOTSTRAP_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        severity=IssueSeverity.WARNING,
        translation_key="bootstrap_detected",
        translation_placeholders={"count": str(len(entries))},
    )


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the ``probe`` service once."""
    store: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if store.get(DATA_SERVICES):
        return
    store[DATA_SERVICES] = True

    async def _async_probe_service(call: ServiceCall) -> dict[str, Any]:
        try:
            spec = proxy_spec_from_options(parse_proxy_string(call.data["proxy"]))
        except ValueError as err:
            return {"ok": False, "error": str(err), "hosts": []}
        hosts = [host for host in call.data[CONF_HOSTS] if isinstance(host, str)]
        error = await async_probe(spec, hosts)
        return {"ok": error is None, "error": error, "hosts": hosts}

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROBE,
        _async_probe_service,
        schema=PROBE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _redact_options(options: dict[str, Any]) -> dict[str, Any]:
    """Return ``options`` without the password."""
    redacted = dict(options)
    password = redacted.pop(CONF_PASSWORD, None)
    redacted["password_set"] = password not in (None, "")
    return redacted


# Kept importable for diagnostics/tests without pulling in more modules.
__all__ = [
    "CONFIG_SCHEMA",
    "PLATFORMS",
    "PROBE_SCHEMA",
    "ProxyError",
    "async_remove_entry",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]

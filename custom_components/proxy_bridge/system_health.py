"""Expose Proxy Bridge routing in the System Health panel."""

from __future__ import annotations

from typing import Any

from homeassistant.components.system_health import SystemHealthRegistration
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, VERSION
from .proxy_core import registered_routes


@callback
def async_register(hass: HomeAssistant, register: SystemHealthRegistration) -> None:
    """Register the info callback for this integration."""

    async def _async_info(hass: HomeAssistant) -> dict[str, Any]:
        routes = [
            {
                "hosts": list(getattr(route, "hosts", ())),
                "upstream": getattr(route, "upstream", None),
                "retries": getattr(route, "retries", None),
            }
            for route in registered_routes()
        ]
        return {
            "version": VERSION,
            "active_routes": len(routes),
            "routes": routes,
            "entries": len(hass.config_entries.async_entries(DOMAIN)),
        }

    register.async_register_info(_async_info)

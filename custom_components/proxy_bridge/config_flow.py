"""Config, options, reconfigure and YAML-import flows for Proxy Bridge."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    BOOTSTRAP_DOMAIN,
    CONF_IMPORTED_FROM,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_TEST_CONNECTION,
    DEFAULT_HOST,
    DEFAULT_HOSTS,
    DEFAULT_PORT,
    DEFAULT_PROXY_TYPE,
    DEFAULT_RETRIES,
    DOMAIN,
    HOST_PRESETS,
    NAME,
    PROXY_TYPES,
    VERSION,
    effective_options,
)
from .proxy_core import (
    CONF_ENABLED,
    CONF_HOST,
    CONF_HOSTS,
    CONF_INSECURE,
    CONF_PORT,
    CONF_PROXY_TYPE,
    CONF_RETRIES,
    CONF_USERNAME,
    ProxySpec,
    async_probe,
    clean_options,
    normalize_host,
    parse_proxy_string,
)

_LOGGER = logging.getLogger(__name__)

CONNECTION_KEYS = (
    CONF_PROXY_TYPE,
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_INSECURE,
)
ROUTES_KEYS = (CONF_NAME, CONF_HOSTS, CONF_RETRIES, CONF_ENABLED)

DEFAULT_CONNECTION: dict[str, Any] = {
    CONF_PROXY_TYPE: DEFAULT_PROXY_TYPE,
    CONF_HOST: DEFAULT_HOST,
    CONF_PORT: DEFAULT_PORT,
    CONF_USERNAME: "",
    CONF_PASSWORD: "",
    CONF_INSECURE: False,
}

DEFAULT_ROUTES: dict[str, Any] = {
    CONF_NAME: NAME,
    CONF_HOSTS: list(DEFAULT_HOSTS),
    CONF_RETRIES: DEFAULT_RETRIES,
    CONF_ENABLED: True,
}

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROXY_TYPE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(PROXY_TYPES),
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="proxy_type",
            )
        ),
        vol.Required(CONF_HOST): selector.TextSelector(
            selector.TextSelectorConfig(autocomplete="url")
        ),
        vol.Required(CONF_PORT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=65535, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Optional(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(autocomplete="username")
        ),
        vol.Optional(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
        vol.Optional(CONF_INSECURE): selector.BooleanSelector(),
    }
)

ROUTES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        vol.Required(CONF_HOSTS): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(HOST_PRESETS),
                multiple=True,
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_RETRIES): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=10, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Optional(CONF_ENABLED): selector.BooleanSelector(),
        vol.Optional(CONF_TEST_CONNECTION): selector.BooleanSelector(),
    }
)


def _validate_connection(data: dict[str, Any]) -> dict[str, str]:
    """Check the upstream fields without touching the network."""
    errors: dict[str, str] = {}
    if data.get(CONF_PROXY_TYPE) not in PROXY_TYPES:
        errors[CONF_PROXY_TYPE] = "invalid_proxy_type"

    host = normalize_host(data.get(CONF_HOST))
    if not host:
        errors[CONF_HOST] = "invalid_host"

    try:
        port = int(data.get(CONF_PORT))
    except (TypeError, ValueError):
        port = 0
    if not 0 < port < 65536:
        errors[CONF_PORT] = "invalid_port"

    username = str(data.get(CONF_USERNAME) or "").strip()
    password = str(data.get(CONF_PASSWORD) or "")
    if password and not username:
        errors[CONF_USERNAME] = "password_without_username"
    return errors


def _spec_from(clean: dict[str, Any]) -> ProxySpec:
    """Build a :class:`ProxySpec` from sanitized settings."""
    return ProxySpec(
        type=clean[CONF_PROXY_TYPE],
        host=clean[CONF_HOST],
        port=int(clean[CONF_PORT]),
        username=clean.get(CONF_USERNAME),
        password=clean.get(CONF_PASSWORD),
        insecure=bool(clean.get(CONF_INSECURE)),
    )


class _CredentialFormMixin:
    """Keep stored proxy credentials out of the rendered forms."""

    _stored_password: str
    _stored_username: str
    _connection: dict[str, Any]

    def _remember_credentials(self, options: dict[str, Any]) -> None:
        """Keep the stored password but never render it back into a form."""
        self._stored_password = str(options.get(CONF_PASSWORD) or "")
        self._stored_username = str(options.get(CONF_USERNAME) or "").strip()

    def _connection_defaults(self) -> dict[str, Any]:
        """Suggested values for the connection step."""
        defaults = {**DEFAULT_CONNECTION, **self._connection}
        if self._stored_password:
            defaults[CONF_PASSWORD] = ""
        return defaults

    def _merge_credentials(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Reuse the stored password when the user left the field blank."""
        data = dict(user_input)
        if not data.get(CONF_PASSWORD) and self._stored_password:
            username = str(data.get(CONF_USERNAME) or "").strip()
            if username == self._stored_username:
                data[CONF_PASSWORD] = self._stored_password
        return data


class ProxyBridgeConfigFlow(_CredentialFormMixin, ConfigFlow, domain=DOMAIN):
    """Add a proxy route."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._connection: dict[str, Any] = {}
        self._routes: dict[str, Any] = {}
        self._reconfigure = False
        self._imported_from_bootstrap = False
        self._stored_password = ""
        self._stored_username = ""

    # -- entry points -----------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer importing an existing bootstrap entry, else start fresh."""
        if user_input is not None:
            if user_input.get("next_step_id") == "bootstrap_import":
                return await self.async_step_bootstrap_import()
            return await self.async_step_manual()

        if self._bootstrap_entries():
            return self.async_show_menu(
                step_id="user", menu_options=["bootstrap_import", "manual"]
            )
        return await self.async_step_manual()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start from an empty form."""
        return await self.async_step_connection(user_input)

    async def async_step_bootstrap_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prefill everything from the bootstrap integration."""
        entries = self._bootstrap_entries()
        if not entries:
            return self.async_abort(reason="import_failed")

        options = effective_options(entries[0])
        self._imported_from_bootstrap = True
        self._connection = {
            key: options.get(key, DEFAULT_CONNECTION[key]) for key in CONNECTION_KEYS
        }
        self._routes = {
            key: options.get(key, DEFAULT_ROUTES[key]) for key in ROUTES_KEYS
        }
        self._remember_credentials(options)
        return await self.async_step_connection()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit an existing entry."""
        entry = self._get_reconfigure_entry()
        options = effective_options(entry)
        self._reconfigure = True
        if not self._connection:
            self._connection = {
                key: options.get(key, DEFAULT_CONNECTION[key])
                for key in CONNECTION_KEYS
            }
            self._routes = {
                key: options.get(key, DEFAULT_ROUTES[key]) for key in ROUTES_KEYS
            }
            self._remember_credentials(options)
        return await self.async_step_connection(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ProxyBridgeOptionsFlow:
        """Return the options flow (settings can change at runtime)."""
        return ProxyBridgeOptionsFlow()

    # -- shared steps -----------------------------------------------------

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the upstream proxy details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = self._merge_credentials(user_input)
            errors = _validate_connection(data)
            self._connection = {key: data.get(key) for key in CONNECTION_KEYS}
            if not errors:
                return await self.async_step_routes()

        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(
                CONNECTION_SCHEMA, self._connection_defaults()
            ),
            errors=errors,
            description_placeholders={"version": VERSION},
        )

    async def async_step_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect which hosts to route, and test the uplink."""
        errors: dict[str, str] = {}
        placeholders = {"version": VERSION, "error": ""}

        if user_input is not None:
            routes = {key: user_input.get(key) for key in ROUTES_KEYS}
            test_connection = bool(user_input.get(CONF_TEST_CONNECTION, True))
            raw = {**self._connection, **routes}
            clean, errors = clean_options(raw)
            if not errors and test_connection:
                error = await async_probe(_spec_from(clean), clean[CONF_HOSTS])
                if error:
                    errors["base"] = "cannot_connect"
                    placeholders["error"] = error
            if not errors:
                return await self._async_finish(clean, test_connection)
            self._routes = routes

        defaults = {**DEFAULT_ROUTES, **self._routes}
        defaults[CONF_TEST_CONNECTION] = True
        return self.async_show_form(
            step_id="routes",
            data_schema=self.add_suggested_values_to_schema(ROUTES_SCHEMA, defaults),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_finish(
        self, clean: dict[str, Any], test_connection: bool
    ) -> ConfigFlowResult:
        """Persist the collected settings."""
        if not test_connection:
            _LOGGER.warning(
                "Proxy Bridge: connectivity test skipped for %s", clean[CONF_NAME]
            )
        if self._imported_from_bootstrap:
            clean[CONF_IMPORTED_FROM] = BOOTSTRAP_DOMAIN

        if self._reconfigure:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), title=clean[CONF_NAME], options=clean
            )

        await self.async_set_unique_id(slugify(clean[CONF_NAME]))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=clean[CONF_NAME], data=clean, options={})

    # -- YAML import ------------------------------------------------------

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import a ``proxy_bridge:`` YAML block."""
        raw = dict(import_config)
        try:
            spec = parse_proxy_string(raw.pop("proxy"))
        except (KeyError, ValueError) as err:
            _LOGGER.error("Proxy Bridge: cannot import YAML entry %s: %s", raw, err)
            return self.async_abort(reason="import_failed")

        raw.update(spec)
        clean, errors = clean_options(raw)
        if errors:
            _LOGGER.error("Proxy Bridge: invalid YAML entry %s: %s", raw, errors)
            return self.async_abort(reason="import_failed")

        await self.async_set_unique_id(slugify(clean[CONF_NAME]))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=clean[CONF_NAME], data=clean, options={})

    # -- helpers ----------------------------------------------------------

    def _bootstrap_entries(self) -> list[ConfigEntry]:
        """Return installed bootstrap entries, if any."""
        return self.hass.config_entries.async_entries(BOOTSTRAP_DOMAIN)


class ProxyBridgeOptionsFlow(_CredentialFormMixin, OptionsFlow):
    """Edit an existing proxy route from the integration card."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._connection: dict[str, Any] = {}
        self._routes: dict[str, Any] = {}
        self._stored_password = ""
        self._stored_username = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Seed the forms from the current settings."""
        options = effective_options(self.config_entry)
        self._connection = {
            key: options.get(key, DEFAULT_CONNECTION[key]) for key in CONNECTION_KEYS
        }
        self._routes = {
            key: options.get(key, DEFAULT_ROUTES[key]) for key in ROUTES_KEYS
        }
        self._remember_credentials(options)
        return await self.async_step_connection(user_input)

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the upstream proxy details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = self._merge_credentials(user_input)
            errors = _validate_connection(data)
            self._connection = {key: data.get(key) for key in CONNECTION_KEYS}
            if not errors:
                return await self.async_step_routes()

        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(
                CONNECTION_SCHEMA, self._connection_defaults()
            ),
            errors=errors,
            description_placeholders={"version": VERSION},
        )

    async def async_step_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the routed hosts and save."""
        errors: dict[str, str] = {}
        placeholders = {"version": VERSION, "error": ""}

        if user_input is not None:
            routes = {key: user_input.get(key) for key in ROUTES_KEYS}
            test_connection = bool(user_input.get(CONF_TEST_CONNECTION, True))
            raw = {**self._connection, **routes}
            clean, errors = clean_options(raw)
            if not errors and test_connection:
                error = await async_probe(_spec_from(clean), clean[CONF_HOSTS])
                if error:
                    errors["base"] = "cannot_connect"
                    placeholders["error"] = error
            if not errors:
                # OptionsFlow.async_create_entry writes entry.options and reloads
                # the entry; the title lives outside options, so update it first.
                self.hass.config_entries.async_update_entry(
                    self.config_entry, title=clean[CONF_NAME]
                )
                return self.async_create_entry(title="", data=clean)
            self._routes = routes

        defaults = {**DEFAULT_ROUTES, **self._routes}
        defaults[CONF_TEST_CONNECTION] = True
        return self.async_show_form(
            step_id="routes",
            data_schema=self.add_suggested_values_to_schema(ROUTES_SCHEMA, defaults),
            errors=errors,
            description_placeholders=placeholders,
        )

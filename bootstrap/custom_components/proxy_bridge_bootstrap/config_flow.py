"""Config flow for the bootstrap copy (kept deliberately small)."""

from __future__ import annotations

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

from .const import DOMAIN, NAME, VERSION, effective_options
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
    HOST_PRESETS,
    PROXY_TYPES,
    ProxySpec,
    async_probe,
    clean_options,
    normalize_host,
)

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

    if not normalize_host(data.get(CONF_HOST)):
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

    def _apply_connection(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Validate the connection step and remember the result."""
        data = self._merge_credentials(user_input)
        errors = _validate_connection(data)
        self._connection = {key: data.get(key) for key in CONNECTION_KEYS}
        return errors, data

    def _show_connection(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Render the connection step."""
        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(
                CONNECTION_SCHEMA, self._connection_defaults()
            ),
            errors=errors,
            description_placeholders={"version": VERSION},
        )


class BootstrapConfigFlow(_CredentialFormMixin, ConfigFlow, domain=DOMAIN):
    """Add a proxy route with the bootstrap copy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._connection: dict[str, Any] = {}
        self._routes: dict[str, Any] = {}
        self._stored_password = ""
        self._stored_username = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start with the upstream proxy."""
        return await self.async_step_connection(user_input)

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the upstream proxy details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = self._apply_connection(user_input)
            if not errors:
                return await self.async_step_routes()
        return self._show_connection(errors)

    async def async_step_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the routed hosts and save."""
        errors: dict[str, str] = {}
        placeholders = {"version": VERSION, "error": ""}

        if user_input is not None:
            routes = {key: user_input.get(key) for key in ROUTES_KEYS}
            test_connection = bool(user_input.get(CONF_TEST_CONNECTION, True))
            clean, errors = clean_options({**self._connection, **routes})
            if not errors and test_connection:
                error = await async_probe(_spec_from(clean), clean[CONF_HOSTS])
                if error:
                    errors["base"] = "cannot_connect"
                    placeholders["error"] = error
            if not errors:
                await self.async_set_unique_id(slugify(clean[CONF_NAME]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=clean[CONF_NAME], data=clean, options={}
                )
            self._routes = routes

        defaults = {**DEFAULT_ROUTES, **self._routes}
        defaults[CONF_TEST_CONNECTION] = True
        return self.async_show_form(
            step_id="routes",
            data_schema=self.add_suggested_values_to_schema(ROUTES_SCHEMA, defaults),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BootstrapOptionsFlow:
        """Return the options flow."""
        return BootstrapOptionsFlow()


class BootstrapOptionsFlow(_CredentialFormMixin, OptionsFlow):
    """Edit the bootstrap entry."""

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
            errors, _ = self._apply_connection(user_input)
            if not errors:
                return await self.async_step_routes()
        return self._show_connection(errors)

    async def async_step_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the routed hosts and save."""
        errors: dict[str, str] = {}
        placeholders = {"version": VERSION, "error": ""}

        if user_input is not None:
            routes = {key: user_input.get(key) for key in ROUTES_KEYS}
            test_connection = bool(user_input.get(CONF_TEST_CONNECTION, True))
            clean, errors = clean_options({**self._connection, **routes})
            if not errors and test_connection:
                error = await async_probe(_spec_from(clean), clean[CONF_HOSTS])
                if error:
                    errors["base"] = "cannot_connect"
                    placeholders["error"] = error
            if not errors:
                # The title lives outside options: update it, then let the
                # options flow write entry.options and reload the entry.
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

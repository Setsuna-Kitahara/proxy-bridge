"""Config flow, options flow, setup and repair tests on a real HA instance.

These tests need ``pytest-homeassistant-custom-component``:

    pip install pytest-homeassistant-custom-component
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proxy_bridge.const import (
    BOOTSTRAP_DOMAIN,
    BOOTSTRAP_ISSUE_ID,
    DOMAIN,
)
from custom_components.proxy_bridge.proxy_core import (
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
    registered_routes,
    unpatch,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

CONNECTION = {
    CONF_PROXY_TYPE: "http",
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 7890,
    CONF_USERNAME: "",
    CONF_PASSWORD: "",
    CONF_INSECURE: False,
}

ROUTES = {
    CONF_NAME: "Home proxy",
    CONF_HOSTS: ["raw.githubusercontent.com"],
    CONF_RETRIES: 2,
    CONF_ENABLED: True,
    # Never touch the network in tests.
    CONF_TEST_CONNECTION: False,
}

STORED = {
    **CONNECTION,
    CONF_NAME: "Home proxy",
    CONF_HOSTS: ["raw.githubusercontent.com"],
    CONF_RETRIES: 2,
    CONF_ENABLED: True,
}


@pytest.fixture(autouse=True)
def _reset_patch():
    """Undo the aiohttp patch between tests."""
    yield
    unpatch()


@pytest.fixture
def managed_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a configured entry without starting it."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Home proxy", data=STORED, unique_id="home_proxy"
    )
    entry.add_to_hass(hass)
    return entry


async def test_user_flow_creates_entry(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """The two-step wizard stores all settings."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "connection"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONNECTION
    )
    assert result["step_id"] == "routes"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], ROUTES)
    assert result["type"] == "create_entry"
    assert result["title"] == "Home proxy"
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["data"][CONF_HOSTS] == ["raw.githubusercontent.com"]
    assert result["data"][CONF_PASSWORD] is None


async def test_connection_step_rejects_bad_input(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Validation errors keep the user on the first step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    # A pasted proxy URL is accepted (normalized); an empty authority is not.
    bad = {**CONNECTION, CONF_HOST: "http://"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["type"] == "form"
    assert result["step_id"] == "connection"
    assert result["errors"] == {CONF_HOST: "invalid_host"}


@pytest.mark.parametrize("port", [0, 70000])
def test_port_validation_bounds(port: int) -> None:
    """The port range is enforced outside the selector too (YAML, services)."""
    from custom_components.proxy_bridge.config_flow import _validate_connection

    errors = _validate_connection({**CONNECTION, CONF_PORT: port})
    assert errors == {CONF_PORT: "invalid_port"}


async def test_password_requires_username(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """A password without a user name is rejected early."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONNECTION, CONF_PASSWORD: "secret"}
    )
    assert result["errors"] == {CONF_USERNAME: "password_without_username"}


async def test_duplicate_name_is_rejected(
    hass: HomeAssistant, managed_entry: MockConfigEntry, enable_custom_integrations
) -> None:
    """The name doubles as the unique id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONNECTION
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ROUTES)
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_yaml_import(hass: HomeAssistant, enable_custom_integrations) -> None:
    """A proxy URL from YAML becomes a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data={
            "proxy": "socks5h://user:pass@192.0.2.10:1080",
            "name": "Imported",
            "hosts": ["api.github.com"],
            "retries": 1,
        },
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PROXY_TYPE] == "socks5h"
    assert result["data"][CONF_USERNAME] == "user"
    assert result["data"][CONF_PORT] == 1080


async def test_yaml_import_rejects_bad_proxy(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """An unknown scheme aborts the import instead of creating junk."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data={"proxy": "ftp://192.0.2.10:21"},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "import_failed"


async def test_bootstrap_import_is_offered(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """An installed bootstrap copy can seed the managed entry."""
    bootstrap = MockConfigEntry(
        domain=BOOTSTRAP_DOMAIN,
        title="Bootstrap",
        data={**STORED, CONF_HOST: "192.0.2.99"},
    )
    bootstrap.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "menu"
    assert set(result["menu_options"]) == {"bootstrap_import", "manual"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "bootstrap_import"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "connection"
    schema = result["data_schema"].schema
    host_key = next(key for key in schema if str(key) == CONF_HOST)
    assert host_key.description["suggested_value"] == "192.0.2.99"


async def test_options_flow_updates_entry(
    hass: HomeAssistant, managed_entry: MockConfigEntry, enable_custom_integrations
) -> None:
    """Options are written to entry.options and reload the entry."""
    result = await hass.config_entries.options.async_init(managed_entry.entry_id)
    assert result["step_id"] == "connection"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**CONNECTION, CONF_HOST: "192.0.2.77"}
    )
    assert result["step_id"] == "routes"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ROUTES, CONF_HOSTS: ["github.com"]}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert managed_entry.options[CONF_HOST] == "192.0.2.77"
    assert managed_entry.options[CONF_HOSTS] == ["github.com"]


async def test_setup_and_unload_route_traffic(
    hass: HomeAssistant,
    managed_entry: MockConfigEntry,
    enable_custom_integrations,
    socket_enabled,
) -> None:
    """Setup starts a bridge and registers the route; unload reverses it."""
    assert await hass.config_entries.async_setup(managed_entry.entry_id)
    await hass.async_block_till_done()

    assert managed_entry.state is ConfigEntryState.LOADED
    runtime = managed_entry.runtime_data
    assert runtime is not None and runtime.started
    assert runtime.bridge.url.startswith("http://127.0.0.1:")
    assert [rule.name for rule in registered_routes()] == [managed_entry.entry_id]

    assert await hass.config_entries.async_unload(managed_entry.entry_id)
    await hass.async_block_till_done()
    assert managed_entry.state is ConfigEntryState.NOT_LOADED
    assert registered_routes() == ()


async def test_disabled_entry_starts_nothing(
    hass: HomeAssistant, enable_custom_integrations, socket_enabled
) -> None:
    """A disabled route keeps the entry loaded but routes nothing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Off",
        data={**STORED, CONF_ENABLED: False},
        unique_id="off",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is None
    assert registered_routes() == ()


async def test_bootstrap_repair_issue_lifecycle(
    hass: HomeAssistant,
    managed_entry: MockConfigEntry,
    enable_custom_integrations,
    socket_enabled,
) -> None:
    """The repair issue appears next to a bootstrap entry and disappears."""
    from homeassistant.helpers import issue_registry as ir

    bootstrap = MockConfigEntry(domain=BOOTSTRAP_DOMAIN, title="Bootstrap", data=STORED)
    bootstrap.add_to_hass(hass)

    assert await hass.config_entries.async_setup(managed_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, BOOTSTRAP_ISSUE_ID) is not None

    await hass.config_entries.async_remove(bootstrap.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, BOOTSTRAP_ISSUE_ID) is None


async def test_options_flow_keeps_password_when_blank(
    hass: HomeAssistant, managed_entry: MockConfigEntry, enable_custom_integrations
) -> None:
    """Editing without retyping the password reuses the stored one."""
    hass.config_entries.async_update_entry(
        managed_entry,
        data={**STORED, CONF_USERNAME: "proxyuser", CONF_PASSWORD: "s3cret"},
    )
    result = await hass.config_entries.options.async_init(managed_entry.entry_id)
    assert result["step_id"] == "connection"
    # The stored password must not be rendered back into the form.
    password_key = next(k for k in result["data_schema"].schema if k == CONF_PASSWORD)
    assert not (password_key.description or {}).get("suggested_value")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**CONNECTION, CONF_USERNAME: "proxyuser"}
    )
    assert result["step_id"] == "routes"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], ROUTES
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert managed_entry.options[CONF_PASSWORD] == "s3cret"


async def test_options_flow_drops_password_when_username_changes(
    hass: HomeAssistant, managed_entry: MockConfigEntry, enable_custom_integrations
) -> None:
    """A different username must not inherit the old password."""
    hass.config_entries.async_update_entry(
        managed_entry,
        data={**STORED, CONF_USERNAME: "proxyuser", CONF_PASSWORD: "s3cret"},
    )
    result = await hass.config_entries.options.async_init(managed_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**CONNECTION, CONF_USERNAME: "someone-else"}
    )
    assert result["step_id"] == "routes"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], ROUTES
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert not managed_entry.options.get(CONF_PASSWORD)

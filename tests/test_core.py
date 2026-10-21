"""Unit and end-to-end tests for the proxy transport layer."""

from __future__ import annotations

import asyncio
import contextlib

import aiohttp
import proxy_core as core
import pytest
from fakes import (
    ORIGIN_BODY,
    FakeHttpProxy,
    FakeSocks5Proxy,
    OriginServer,
    TlsOriginServer,
    close_writer,
    free_port,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def origin():
    """Plain HTTP origin on loopback."""
    server = OriginServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def echo():
    """TCP echo server on loopback."""

    async def handle(reader, writer):
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            await close_writer(writer)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port
    server.close()
    with contextlib.suppress(Exception):
        await server.wait_closed()


def map_origin(origin) -> dict[str, str]:
    """Fake-proxy mapping: origin.test -> the live origin port."""
    return {"origin.test": f"127.0.0.1:{origin.port}"}


@contextlib.asynccontextmanager
async def bridge_for(spec: core.ProxySpec, hosts: tuple[str, ...], retries: int = 0):
    """Start a bridge and guarantee cleanup."""
    bridge = core.LocalBridge(core.BridgeConfig("test", spec, hosts, retries))
    await bridge.async_start()
    try:
        yield bridge
    finally:
        await bridge.async_stop()


async def http_get(url: str, proxy_url: str) -> tuple[int, bytes]:
    """GET through an explicit proxy and return (status, body)."""
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, proxy=proxy_url) as response:
            return response.status, await response.read()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalize_hosts() -> None:
    assert core.normalize_hosts(
        [
            "https://GitHub.com:443/some/path",
            "user@Raw.GitHubUserContent.com",
            "*.github.com",
            "github.com",
            "github.com.",
            "  ",
        ]
    ) == ("github.com", "raw.githubusercontent.com", "*.github.com")


def test_host_matching() -> None:
    assert core.host_matches("github.com", "github.com")
    assert not core.host_matches("github.com", "api.github.com")
    assert core.host_matches("*.github.com", "api.github.com")
    assert not core.host_matches("*.github.com", "github.com")
    assert core.host_matches("*githubusercontent.com", "raw.githubusercontent.com")
    assert core.host_matches("api.?.com", "api.x.com")


def test_clean_options_defaults() -> None:
    clean, errors = core.clean_options({"hosts": ["github.com"]})
    assert errors == {}
    assert clean[core.CONF_PROXY_TYPE] == "http"
    assert clean[core.CONF_PORT] == core.DEFAULT_PORT
    assert clean[core.CONF_HOSTS] == ["github.com"]
    # An empty host list is not a usable configuration.
    _, errors = core.clean_options({})
    assert errors[core.CONF_HOSTS] == "empty"


def test_clean_options_rejects_bad_input() -> None:
    _, errors = core.clean_options(
        {
            "proxy_type": "ftp",
            "host": "",
            "port": 99999,
            "password": "secret",
            "hosts": [],
            "retries": 99,
        }
    )
    assert set(errors) >= {
        "proxy_type",
        "host",
        "port",
        "username",
        "hosts",
        "retries",
    }


def test_build_bridge_config() -> None:
    config, errors = core.build_bridge_config(
        {
            "name": "Work proxy",
            "proxy_type": "socks5",
            "host": "10.0.0.2",
            "port": 1080,
            "username": "me",
            "password": "p",
            "hosts": [
                "raw.githubusercontent.com",
                "*.github.com",
                "raw.githubusercontent.com",
            ],
            "retries": 1,
        }
    )
    assert errors == {}
    assert config is not None
    assert config.proxy.redacted() == "socks5://me@10.0.0.2:1080"
    assert config.hosts == ("raw.githubusercontent.com", "*.github.com")
    assert config.matches("api.github.com")
    assert config.matches("github.com") is False


def test_parse_proxy_string() -> None:
    assert core.parse_proxy_string("socks5h://u:p@1.2.3.4:1080") == {
        "proxy_type": "socks5h",
        "host": "1.2.3.4",
        "port": 1080,
        "username": "u",
        "password": "p",
    }
    assert core.parse_proxy_string("127.0.0.1:7890")["proxy_type"] == "http"
    with pytest.raises(ValueError):
        core.parse_proxy_string("ftp://example.com")


# ---------------------------------------------------------------------------
# HTTP(S) proxy upstream
# ---------------------------------------------------------------------------


async def test_http_proxy_with_basic_auth(origin) -> None:
    proxy = FakeHttpProxy(username="user", password="pa:ss", mapping=map_origin(origin))
    await proxy.start()
    try:
        spec = core.ProxySpec("http", "127.0.0.1", proxy.port, "user", "pa:ss")
        async with bridge_for(spec, ("origin.test",)) as bridge:
            status, body = await http_get("http://origin.test/", bridge.url)
            assert status == 200
            assert body == ORIGIN_BODY
            assert bridge.stats.requests == 1
            # plain HTTP is forwarded, not tunnelled: no client CONNECT
            assert bridge.stats.connects == 0
            assert bridge.stats.bytes_out > 0
            assert bridge.stats.bytes_in > 0
        assert proxy.connects == ["origin.test:80"]
        assert proxy.auth_headers[0] is not None
        assert proxy.auth_headers[0].startswith("Basic ")
    finally:
        await proxy.stop()


async def test_http_proxy_wrong_credentials(origin) -> None:
    proxy = FakeHttpProxy(username="user", password="right", mapping=map_origin(origin))
    await proxy.start()
    try:
        spec = core.ProxySpec("http", "127.0.0.1", proxy.port, "user", "wrong")
        async with bridge_for(spec, ("origin.test",)) as bridge:
            status, _ = await http_get("http://origin.test/", bridge.url)
            assert status == 502
            assert bridge.stats.errors == 1
            assert "407" in (bridge.stats.last_error or "")
    finally:
        await proxy.stop()


async def test_https_upstream_proxy_insecure(origin) -> None:
    proxy = FakeHttpProxy(mapping=map_origin(origin), secure=True)
    await proxy.start()
    try:
        spec = core.ProxySpec("https", "127.0.0.1", proxy.port, insecure=True)
        async with bridge_for(spec, ("origin.test",)) as bridge:
            status, body = await http_get("http://origin.test/", bridge.url)
            assert status == 200
            assert body == ORIGIN_BODY
    finally:
        await proxy.stop()


async def test_https_upstream_proxy_verifies_by_default(origin) -> None:
    proxy = FakeHttpProxy(mapping=map_origin(origin), secure=True)
    await proxy.start()
    try:
        spec = core.ProxySpec("https", "127.0.0.1", proxy.port, insecure=False)
        async with bridge_for(spec, ("origin.test",)) as bridge:
            status, _ = await http_get("http://origin.test/", bridge.url)
            assert status == 502
    finally:
        await proxy.stop()


async def test_connect_tunnel_relays_raw_bytes(echo, origin) -> None:
    proxy = FakeHttpProxy(mapping={"echo.test": "127.0.0.1"})
    await proxy.start()
    try:
        spec = core.ProxySpec("http", "127.0.0.1", proxy.port)
        async with bridge_for(spec, ("echo.test",)) as bridge:
            reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
            try:
                writer.write(
                    f"CONNECT echo.test:{echo} HTTP/1.1\r\nHost: echo.test\r\n\r\n".encode()
                )
                await writer.drain()
                status = await asyncio.wait_for(reader.readline(), 5)
                assert status.startswith(b"HTTP/1.1 200")
                while (await asyncio.wait_for(reader.readline(), 5)) not in (
                    b"\r\n",
                    b"",
                ):
                    pass
                writer.write(b"ping-through-tunnel")
                await writer.drain()
                assert (
                    await asyncio.wait_for(reader.readexactly(19), 5)
                    == b"ping-through-tunnel"
                )
            finally:
                await close_writer(writer)
    finally:
        await proxy.stop()


# ---------------------------------------------------------------------------
# SOCKS5 upstream
# ---------------------------------------------------------------------------


async def test_socks5h_with_auth_sends_domain(origin) -> None:
    proxy = FakeSocks5Proxy(
        username="sock", password="pass", mapping=map_origin(origin)
    )
    await proxy.start()
    try:
        spec = core.ProxySpec("socks5h", "127.0.0.1", proxy.port, "sock", "pass")
        async with bridge_for(spec, ("origin.test",)) as bridge:
            status, body = await http_get("http://origin.test/", bridge.url)
            assert status == 200
            assert body == ORIGIN_BODY
        assert proxy.targets and proxy.targets[0][0] == 0x03  # domain name
    finally:
        await proxy.stop()


async def test_socks5_resolves_locally(origin) -> None:
    proxy = FakeSocks5Proxy(mapping=map_origin(origin))
    await proxy.start()
    try:
        spec = core.ProxySpec("socks5", "127.0.0.1", proxy.port)
        async with bridge_for(spec, ("127.0.0.1",)) as bridge:
            status, body = await http_get(
                f"http://127.0.0.1:{origin.port}/", bridge.url
            )
            assert status == 200
            assert body == ORIGIN_BODY
        assert proxy.targets and proxy.targets[0][0] == 0x01  # IPv4
    finally:
        await proxy.stop()


async def test_socks5_wrong_credentials(origin) -> None:
    proxy = FakeSocks5Proxy(username="sock", password="right")
    await proxy.start()
    try:
        spec = core.ProxySpec("socks5h", "127.0.0.1", proxy.port, "sock", "wrong")
        async with bridge_for(spec, ("origin.test",)) as bridge:
            status, _ = await http_get("http://origin.test/", bridge.url)
            assert status == 502
            assert "credentials" in (bridge.stats.last_error or "")
    finally:
        await proxy.stop()


async def test_upstream_down(origin) -> None:
    spec = core.ProxySpec("http", "127.0.0.1", 1)  # nothing listens on :1
    async with bridge_for(spec, ("origin.test",)) as bridge:
        status, _ = await http_get("http://origin.test/", bridge.url)
        assert status == 502
        assert bridge.stats.errors == 1


# ---------------------------------------------------------------------------
# aiohttp patch and routing
# ---------------------------------------------------------------------------


@pytest.fixture
async def proxy_route(origin):
    """Fake upstream + registered route for ``origin.test``."""
    proxy = FakeHttpProxy(mapping=map_origin(origin))
    await proxy.start()
    spec = core.ProxySpec("http", "127.0.0.1", proxy.port)
    runtime = core.ProxyRuntime(
        core.BridgeConfig("patched", spec, ("origin.test",), 0), "entry-1"
    )
    await runtime.async_start()
    try:
        yield proxy, runtime
    finally:
        await runtime.async_stop()
        await proxy.stop()
        core.unpatch()


def test_patch_is_installed_once(proxy_route) -> None:
    first = aiohttp.ClientSession._request
    core.ensure_patch()
    assert aiohttp.ClientSession._request is first
    assert getattr(first, core._PATCH_FLAG) is True


async def test_patched_session_routes_matching_host(proxy_route, origin) -> None:
    proxy, runtime = proxy_route
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("http://origin.test/") as response:
            assert response.status == 200
            assert await response.read() == ORIGIN_BODY
    assert runtime.bridge.stats.requests == 1
    assert proxy.connects == ["origin.test:80"]


async def test_patched_session_leaves_other_hosts_alone(proxy_route, origin) -> None:
    proxy, runtime = proxy_route
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"http://127.0.0.1:{origin.port}/") as response:
            assert response.status == 200
    assert runtime.bridge.stats.requests == 0
    assert proxy.connects == []


async def test_patched_session_respects_explicit_proxy(proxy_route, origin) -> None:
    _proxy, runtime = proxy_route
    other = FakeHttpProxy(mapping=map_origin(origin))
    await other.start()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                "http://origin.test/", proxy=f"http://127.0.0.1:{other.port}"
            ) as response,
        ):
            # the fake proxy only speaks CONNECT, so an untouched plain
            # request shows up as 405 - which is exactly the point: the
            # explicit proxy argument wins over the routing table.
            assert response.status == 405
        assert runtime.bridge.stats.requests == 0
    finally:
        await other.stop()


async def test_patch_retries_get(origin) -> None:
    proxy = FakeHttpProxy(mapping=map_origin(origin), fail_connects=1)
    await proxy.start()
    try:
        spec = core.ProxySpec("http", "127.0.0.1", proxy.port)
        runtime = core.ProxyRuntime(
            core.BridgeConfig("retry", spec, ("origin.test",), 1), "entry-retry"
        )
        await runtime.async_start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://origin.test/") as response:
                    assert response.status == 200
            assert proxy.connects == ["origin.test:80"]
            assert proxy.fail_connects == 0  # the first attempt was burned
            assert runtime.bridge.stats.errors == 0
        finally:
            await runtime.async_stop()
    finally:
        await proxy.stop()
        core.unpatch()


async def test_unregister_stops_routing(origin) -> None:
    proxy = FakeHttpProxy(mapping=map_origin(origin))
    await proxy.start()
    runtime = core.ProxyRuntime(
        core.BridgeConfig(
            "gone", core.ProxySpec("http", "127.0.0.1", proxy.port), ("origin.test",)
        ),
        "entry-gone",
    )
    await runtime.async_start()
    assert [route.name for route in core.registered_routes()] == ["entry-gone"]
    await runtime.async_stop()
    assert [route.name for route in core.registered_routes()] == []
    await proxy.stop()
    core.unpatch()


async def test_probe_success_and_failure() -> None:
    tls = TlsOriginServer()
    await tls.start()
    proxy = FakeHttpProxy(mapping={"probe.test": f"127.0.0.1:{tls.port}"})
    await proxy.start()
    try:
        spec = core.ProxySpec("http", "127.0.0.1", proxy.port)
        assert await core.async_probe(spec, ["probe.test"]) is None

        refusing = core.ProxySpec("http", "127.0.0.1", free_port())
        error = await core.async_probe(refusing, ["probe.test"])
        assert error is not None and "probe.test" in error
    finally:
        await proxy.stop()
        await tls.stop()


async def test_probe_reports_bad_credentials() -> None:
    tls = TlsOriginServer()
    await tls.start()
    proxy = FakeHttpProxy(
        username="u", password="right", mapping={"probe.test": f"127.0.0.1:{tls.port}"}
    )
    await proxy.start()
    try:
        spec = core.ProxySpec("http", "127.0.0.1", proxy.port, "u", "wrong")
        error = await core.async_probe(spec, ["probe.test"])
        assert error is not None and "407" in error
    finally:
        await proxy.stop()
        await tls.stop()


# ---------------------------------------------------------------------------
# Config cleanup edge cases
# ---------------------------------------------------------------------------


def test_clean_options_trims_and_dedupes() -> None:
    clean, errors = core.clean_options(
        {
            "name": "  Home  ",
            "proxy_type": "SOCKS5H",
            "host": "[::1]",
            "port": "1080",
            "hosts": "github.com, api.github.com github.com",
            "retries": "0",
        }
    )
    assert errors == {}
    assert clean["name"] == "Home"
    assert clean["proxy_type"] == "socks5h"
    assert clean["host"] == "::1"
    assert clean["hosts"] == ["github.com", "api.github.com"]


def test_redacted_never_leaks_password() -> None:
    spec = core.ProxySpec("http", "10.1.1.1", 3128, "alice", "topsecret")
    assert "topsecret" not in spec.redacted()
    assert "alice" in spec.redacted()
    assert "topsecret" not in str(spec.as_dict())


def test_parse_proxy_string_feeds_proxy_spec() -> None:
    """Option-keyed output of parse_proxy_string builds a ProxySpec."""
    parsed = core.parse_proxy_string("socks5h://user:secret@10.0.0.5:1080")
    spec = core.proxy_spec_from_options(parsed)
    assert spec == core.ProxySpec(
        type="socks5h",
        host="10.0.0.5",
        port=1080,
        username="user",
        password="secret",
    )
    assert core.proxy_spec_from_options({"proxy_type": "http"}) == core.ProxySpec()

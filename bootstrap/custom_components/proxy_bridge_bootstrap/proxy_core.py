"""Transport layer of Proxy Bridge.

This module is deliberately free of Home Assistant imports.  It is shared
byte-for-byte between the managed integration (``proxy_bridge``) and the
bootstrap integration (``proxy_bridge_bootstrap``) so both can talk to the
same upstream proxies, and it is exercised directly by the test suite.

How it works
------------
Home Assistant (and HACS) talk to the internet through ``aiohttp``.  Instead
of teaching every caller about proxies, Proxy Bridge starts a tiny HTTP proxy
on ``127.0.0.1`` with a random port and patches ``aiohttp.ClientSession``
once: when a request targets one of the configured hosts, the patch adds
``proxy="http://127.0.0.1:<port>"`` to the request.  ``aiohttp`` then sends
``CONNECT host:443`` to our local bridge, the bridge opens a tunnel through
the real upstream proxy (HTTP, HTTPS or SOCKS5) and relays bytes.

The bridge answers plain ``http://`` requests as well: the absolute request
URI is rewritten to origin form and forwarded through the same tunnel, so a
single code path covers both schemes.

Supported upstreams
-------------------
* ``http`` / ``https``  - CONNECT tunnel, optional ``Proxy-Authorization: Basic``.
* ``socks5h``           - RFC 1928, host names are resolved by the proxy.
* ``socks5``            - RFC 1928, host names are resolved locally first.

No third-party dependencies are required beyond ``aiohttp`` itself.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import logging
import re
import socket
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration keys (single source of truth, re-exported by const.py)
# ---------------------------------------------------------------------------
CONF_NAME = "name"
CONF_PROXY_TYPE = "proxy_type"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_HOSTS = "hosts"
CONF_RETRIES = "retries"
CONF_INSECURE = "insecure"
CONF_ENABLED = "enabled"
CONF_TEST_CONNECTION = "test_connection"

PROXY_TYPES: tuple[str, ...] = ("http", "https", "socks5", "socks5h")
DEFAULT_PROXY_TYPE = "http"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_RETRIES = 2
DEFAULT_TIMEOUT = 15.0

# Hosts that make up "GitHub + HACS essentials".  The first one is used as the
# connection-test target.
DEFAULT_HOSTS: tuple[str, ...] = (
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "github.com",
    "api.github.com",
    "gist.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "data-v2.hacs.xyz",
)

# Presets offered in the UI.  Users can type any extra pattern.
HOST_PRESETS: tuple[str, ...] = (
    "raw.githubusercontent.com",
    "*.githubusercontent.com",
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "gist.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "avatars.githubusercontent.com",
    "data-v2.hacs.xyz",
    "pypi.org",
    "files.pythonhosted.org",
)

_HOST_INVALID_CHARS = re.compile(r"[\s/\\]")
_SCHEME_PREFIX = re.compile(r"^[a-z][a-z0-9+.-]*://")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProxyError(Exception):
    """The local bridge could not reach the target through the upstream."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def normalize_host(value: Any) -> str | None:
    """Normalize one host entry or pattern from user input.

    ``https://User@GitHub.com:443/x`` -> ``github.com``; ``*.github.com`` is
    kept as a pattern.  Returns ``None`` for empty input.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    text = _SCHEME_PREFIX.sub("", text)
    text = text.split("/", 1)[0]
    text = text.rsplit("@", 1)[-1]
    if text.startswith("["):  # [::1]:8080
        end = text.find("]")
        text = text[1:end] if end != -1 else text[1:]
    elif text.count(":") == 1:  # host:port
        text = text.split(":", 1)[0]
    text = text.strip().rstrip(".")
    return text or None


def normalize_hosts(values: Iterable[Any]) -> tuple[str, ...]:
    """Normalize and de-duplicate a list of host entries, keeping order."""
    seen: dict[str, None] = {}
    for value in values:
        host = normalize_host(value)
        if host:
            seen.setdefault(host, None)
    return tuple(seen)


def host_matches(pattern: str, host: str) -> bool:
    """Return True when ``host`` matches a pattern (exact or wildcard)."""
    host = host.strip().lower().rstrip(".")
    if not pattern:
        return False
    if pattern.startswith("*."):
        return host.endswith(pattern[1:])
    if "*" in pattern or "?" in pattern or "[" in pattern:
        return fnmatchcase(host, pattern)
    return host == pattern


def format_authority(host: str, port: int) -> str:
    """Format ``host:port`` for CONNECT, bracketing IPv6 literals."""
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def split_authority(authority: str, default_port: int = 443) -> tuple[str, int]:
    """Split ``host:port`` / ``[v6]:port`` into a tuple."""
    authority = authority.strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end == -1:
            raise ProxyError(f"invalid authority {authority!r}")
        host = authority[1:end]
        rest = authority[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:] else default_port
        return host, port
    if authority.count(":") == 1:
        host, port_text = authority.rsplit(":", 1)
        if port_text.isdigit():
            return host, int(port_text)
    return authority, default_port


def _is_loopback(host: str) -> bool:
    if host in ("localhost", "localhost.localdomain", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Proxy specification / bridge configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProxySpec:
    """Connection details of one upstream proxy."""

    type: str = DEFAULT_PROXY_TYPE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    username: str | None = None
    password: str | None = None
    insecure: bool = False

    def __post_init__(self) -> None:
        if self.type not in PROXY_TYPES:
            raise ValueError(f"unsupported proxy type {self.type!r}")
        if not 0 < int(self.port) < 65536:
            raise ValueError("proxy port out of range")

    @property
    def has_auth(self) -> bool:
        """Whether a username was configured."""
        return bool(self.username)

    def redacted(self) -> str:
        """Human readable ``type://[user@]host:port`` without the password."""
        user = f"{self.username}@" if self.username else ""
        return f"{self.type}://{user}{format_authority(self.host, int(self.port))}"

    def as_dict(self) -> dict[str, Any]:
        """Serializable form; the password is replaced by a boolean."""
        return {
            "type": self.type,
            "host": self.host,
            "port": int(self.port),
            "username": self.username,
            "password_set": bool(self.password),
            "insecure": self.insecure,
        }


def parse_proxy_string(value: str) -> dict[str, Any]:
    """Parse ``type://user:pass@host:port`` into option keys.

    Accepts ``http://``, ``https://``, ``socks5://`` and ``socks5h://`` with an
    optional port (defaults to 8080 for HTTP-left blank).  A bare
    ``host:port`` is treated as ``http``.
    """
    text = value.strip()
    if not text:
        return {}
    if "://" not in text:
        text = f"http://{text}"
    parts = urlsplit(text)
    scheme = (parts.scheme or "http").lower()
    if scheme not in PROXY_TYPES:
        raise ValueError(f"unsupported proxy scheme {scheme!r}")
    if not parts.hostname:
        raise ValueError("missing proxy host")
    port = parts.port or (1080 if scheme.startswith("socks") else 8080)
    result: dict[str, Any] = {
        CONF_PROXY_TYPE: scheme,
        CONF_HOST: parts.hostname,
        CONF_PORT: int(port),
    }
    if parts.username:
        result[CONF_USERNAME] = parts.username
    if parts.password:
        result[CONF_PASSWORD] = parts.password
    return result


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """Everything one bridge instance needs."""

    name: str
    proxy: ProxySpec
    hosts: tuple[str, ...]
    retries: int = DEFAULT_RETRIES

    def matches(self, host: str) -> bool:
        """Return True when ``host`` should be routed through the proxy."""
        return any(host_matches(pattern, host) for pattern in self.hosts)


def clean_options(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate and normalize user/imported settings.

    Returns ``(clean, errors)`` where ``errors`` maps translation keys to the
    offending value, ready to be passed to ``async_show_form(errors=...)``.
    """
    errors: dict[str, str] = {}

    name = str(raw.get(CONF_NAME) or "").strip() or "Proxy Bridge"

    proxy_type = str(raw.get(CONF_PROXY_TYPE) or DEFAULT_PROXY_TYPE).strip().lower()
    if proxy_type not in PROXY_TYPES:
        errors[CONF_PROXY_TYPE] = str(raw.get(CONF_PROXY_TYPE))

    raw_host = raw.get(CONF_HOST)
    host = normalize_host(DEFAULT_HOST if raw_host is None else raw_host)
    if not host:
        errors[CONF_HOST] = str(raw.get(CONF_HOST))
    elif _HOST_INVALID_CHARS.search(host):
        errors[CONF_HOST] = host

    try:
        port = int(raw.get(CONF_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = 0
    if not 0 < port < 65536:
        errors[CONF_PORT] = str(raw.get(CONF_PORT))

    username = raw.get(CONF_USERNAME)
    username = str(username).strip() if username not in (None, "") else None
    password = raw.get(CONF_PASSWORD)
    password = str(password) if password not in (None, "") else None
    if password is not None and username is None:
        errors[CONF_USERNAME] = "password without username"

    raw_hosts = raw.get(CONF_HOSTS) or ()
    if isinstance(raw_hosts, str):
        raw_hosts = [part for part in re.split(r"[,\s]+", raw_hosts) if part]
    hosts = normalize_hosts(raw_hosts)
    bad_hosts = [entry for entry in hosts if _HOST_INVALID_CHARS.search(entry)]
    if not hosts or bad_hosts:
        errors[CONF_HOSTS] = ", ".join(bad_hosts) or "empty"

    try:
        retries = int(raw.get(CONF_RETRIES, DEFAULT_RETRIES))
    except (TypeError, ValueError):
        retries = -1
    if not 0 <= retries <= 10:
        errors[CONF_RETRIES] = str(raw.get(CONF_RETRIES))

    clean: dict[str, Any] = {
        CONF_NAME: name,
        CONF_PROXY_TYPE: proxy_type
        if proxy_type in PROXY_TYPES
        else DEFAULT_PROXY_TYPE,
        CONF_HOST: host or DEFAULT_HOST,
        CONF_PORT: port or DEFAULT_PORT,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
        CONF_HOSTS: list(hosts),
        CONF_RETRIES: retries if 0 <= retries <= 10 else DEFAULT_RETRIES,
        CONF_INSECURE: bool(raw.get(CONF_INSECURE, False)),
        CONF_ENABLED: bool(raw.get(CONF_ENABLED, True)),
    }
    return clean, errors


def build_bridge_config(
    raw: Mapping[str, Any],
) -> tuple[BridgeConfig | None, dict[str, str]]:
    """Turn raw settings into a :class:`BridgeConfig`."""
    clean, errors = clean_options(raw)
    if errors:
        return None, errors
    spec = ProxySpec(
        type=clean[CONF_PROXY_TYPE],
        host=clean[CONF_HOST],
        port=int(clean[CONF_PORT]),
        username=clean[CONF_USERNAME],
        password=clean[CONF_PASSWORD],
        insecure=clean[CONF_INSECURE],
    )
    return (
        BridgeConfig(
            name=clean[CONF_NAME],
            proxy=spec,
            hosts=tuple(clean[CONF_HOSTS]),
            retries=clean[CONF_RETRIES],
        ),
        {},
    )


# ---------------------------------------------------------------------------
# Upstream connectors
# ---------------------------------------------------------------------------


class UpstreamConnector:
    """Open a stream to ``host:port`` through the configured upstream proxy."""

    def __init__(self, spec: ProxySpec, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._spec = spec
        self._timeout = timeout

    async def connect(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Return a byte stream that is already connected to ``host:port``."""
        if self._spec.type in ("http", "https"):
            return await self._connect_http_proxy(host, port)
        return await self._connect_socks5(host, port)

    # -- generic helpers ---------------------------------------------------
    async def _open_proxy_socket(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        context: ssl.SSLContext | None = None
        if self._spec.type == "https":
            context = ssl.create_default_context()
            if self._spec.insecure:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(
                    self._spec.host,
                    int(self._spec.port),
                    ssl=context,
                    server_hostname=self._spec.host if context else None,
                ),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as err:
            raise ProxyError(
                f"cannot connect to upstream proxy {self._spec.redacted()}: {err}"
            ) from err

    async def _read_exact(self, reader: asyncio.StreamReader, size: int) -> bytes:
        try:
            return await asyncio.wait_for(
                reader.readexactly(size), timeout=self._timeout
            )
        except (asyncio.IncompleteReadError, TimeoutError, OSError) as err:
            raise ProxyError(
                f"upstream proxy closed the handshake early: {err}"
            ) from err

    # -- HTTP(S) proxy -----------------------------------------------------
    async def _connect_http_proxy(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await self._open_proxy_socket()
        authority = format_authority(host, port)
        lines = [
            f"CONNECT {authority} HTTP/1.1",
            f"Host: {authority}",
            "Proxy-Connection: Keep-Alive",
        ]
        if self._spec.has_auth:
            token = base64.b64encode(
                f"{self._spec.username}:{self._spec.password or ''}".encode()
            ).decode("ascii")
            lines.append(f"Proxy-Authorization: Basic {token}")
        try:
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
            status_line = await asyncio.wait_for(
                reader.readline(), timeout=self._timeout
            )
            if not status_line:
                raise ProxyError(
                    "upstream proxy closed the connection without a status"
                )
            parts = status_line.decode("latin-1", "replace").split()
            code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            # Drain the CONNECT response headers.
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
                if line in (b"\r\n", b"\n", b""):
                    break
            if code == 407:
                raise ProxyError(
                    "upstream proxy rejected the credentials (HTTP 407 Proxy Authentication Required)"
                )
            if code != 200:
                raise ProxyError(
                    f"upstream proxy refused CONNECT {authority}: "
                    f"{status_line.decode('latin-1', 'replace').strip()}"
                )
        except (OSError, TimeoutError) as err:
            await _close_writer(writer)
            raise ProxyError(f"CONNECT {authority} failed: {err}") from err
        except ProxyError:
            await _close_writer(writer)
            raise
        return reader, writer

    # -- SOCKS5 ------------------------------------------------------------
    async def _connect_socks5(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._spec.host, int(self._spec.port)),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as err:
            raise ProxyError(
                f"cannot connect to upstream proxy {self._spec.redacted()}: {err}"
            ) from err

        try:
            # Method negotiation.
            methods = [0x00]
            if self._spec.has_auth:
                methods.append(0x02)
            writer.write(bytes([0x05, len(methods), *methods]))
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
            greeting = await self._read_exact(reader, 2)
            if greeting[0] != 0x05:
                raise ProxyError("upstream is not a SOCKS5 proxy")
            if greeting[1] == 0xFF:
                raise ProxyError(
                    "upstream SOCKS5 proxy rejected all offered auth methods"
                )
            if greeting[1] == 0x02:
                user = (self._spec.username or "").encode()
                pwd = (self._spec.password or "").encode()
                if len(user) > 255 or len(pwd) > 255:
                    raise ProxyError("SOCKS5 username/password must be <= 255 bytes")
                writer.write(bytes([0x01, len(user)]) + user + bytes([len(pwd)]) + pwd)
                await asyncio.wait_for(writer.drain(), timeout=self._timeout)
                status = await self._read_exact(reader, 2)
                if status[1] != 0x00:
                    raise ProxyError("upstream SOCKS5 proxy rejected the credentials")

            atyp, addr = await self._socks5_address(host)
            writer.write(
                bytes([0x05, 0x01, 0x00, atyp]) + addr + int(port).to_bytes(2, "big")
            )
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)

            reply = await self._read_exact(reader, 4)
            if reply[0] != 0x05:
                raise ProxyError("malformed SOCKS5 reply")
            if reply[1] != 0x00:
                raise ProxyError(f"SOCKS5 CONNECT failed with code {reply[1]}")
            atyp = reply[3]
            skip = {0x01: 4, 0x04: 16, 0x03: None}.get(atyp)
            if skip is None:
                length = (await self._read_exact(reader, 1))[0]
                skip = length
            await self._read_exact(reader, skip + 2)
        except ProxyError:
            await _close_writer(writer)
            raise
        except (OSError, TimeoutError) as err:
            await _close_writer(writer)
            raise ProxyError(f"SOCKS5 handshake failed: {err}") from err
        return reader, writer

    async def _socks5_address(self, host: str) -> tuple[int, bytes]:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if self._spec.type == "socks5h":
                return 0x03, bytes([len(host)]) + host.encode("ascii", "idna")
            try:
                infos = await asyncio.wait_for(
                    asyncio.get_running_loop().getaddrinfo(
                        host, None, type=socket.SOCK_STREAM
                    ),
                    timeout=self._timeout,
                )
            except (OSError, TimeoutError) as err:
                raise ProxyError(f"cannot resolve {host} locally: {err}") from err
            ip = ipaddress.ip_address(infos[0][4][0])
        if ip.version == 4:
            return 0x01, ip.packed
        return 0x04, ip.packed


# ---------------------------------------------------------------------------
# Local bridge
# ---------------------------------------------------------------------------

_READ_CHUNK = 65536
_MAX_HEADER_BYTES = 65536
_HEADER_TIMEOUT = 30.0
_CLOSE_TIMEOUT = 2.0


async def _close_writer(
    writer: asyncio.StreamWriter, timeout: float = _CLOSE_TIMEOUT
) -> None:
    """Close a stream without ever waiting forever on a TLS shutdown."""
    writer.close()
    transport = writer.transport
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        if transport is not None:
            transport.abort()
    except Exception:
        if transport is not None:
            transport.abort()


@dataclass
class BridgeStats:
    """Counters exposed through diagnostics."""

    requests: int = 0
    connects: int = 0
    errors: int = 0
    bytes_out: int = 0
    bytes_in: int = 0
    last_error: str | None = None
    started_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serializable snapshot."""
        return {
            "requests": self.requests,
            "connects": self.connects,
            "errors": self.errors,
            "bytes_out": self.bytes_out,
            "bytes_in": self.bytes_in,
            "last_error": self.last_error,
            "started_at": self.started_at,
        }


class LocalBridge:
    """Minimal HTTP proxy listening on a loopback port."""

    def __init__(
        self, config: BridgeConfig, *, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        self.config = config
        self._connector = UpstreamConnector(config.proxy, timeout=timeout)
        self.stats = BridgeStats()
        self._server: asyncio.AbstractServer | None = None
        self._port: int | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._stopping = False

    @property
    def port(self) -> int | None:
        """Bound loopback port, ``None`` before start."""
        return self._port

    @property
    def url(self) -> str:
        """Loopback proxy URL handed to aiohttp."""
        return f"http://127.0.0.1:{self._port}"

    async def async_start(self) -> None:
        """Bind to a random loopback port."""
        self.stats.started_at = asyncio.get_running_loop().time()
        self._server = await asyncio.start_server(
            self._handle_client, host="127.0.0.1", port=0, limit=_MAX_HEADER_BYTES
        )
        socket = self._server.sockets[0]
        self._port = socket.getsockname()[1]

    async def async_stop(self) -> None:
        """Close the listener and every in-flight connection.

        Cancellation happens *before* ``Server.wait_closed()`` because since
        Python 3.12 that call blocks until every connection handler finished.
        """
        self._stopping = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        server = self._server
        self._server = None
        if server is not None:
            server.close()
        writers = list(self._writers)
        for writer in writers:
            writer.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for writer in writers:
            await _close_writer(writer)
        if server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=_CLOSE_TIMEOUT)
        self._writers.clear()
        self._tasks.clear()

    # -- connection handling ----------------------------------------------
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        try:
            await self._serve(reader, writer)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("proxy bridge: client connection dropped: %s", err)
        except Exception:
            _LOGGER.exception("proxy bridge: unexpected error while serving a client")
        finally:
            if task is not None:
                self._tasks.discard(task)
            self._writers.discard(writer)
            await _close_writer(writer)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(
                reader.readline(), timeout=_HEADER_TIMEOUT
            )
        except (TimeoutError, ConnectionError):
            return
        if not request_line:
            return
        line = request_line.decode("latin-1").rstrip("\r\n")
        parts = line.split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            await self._send_error(writer, 400, "Bad Request")
            return
        method, target, version = parts
        try:
            headers = await self._read_headers(reader)
        except ProxyError as err:
            await self._send_error(writer, 400, str(err))
            return

        self.stats.requests += 1
        if method.upper() == "CONNECT":
            await self._serve_connect(target, reader, writer)
        else:
            await self._serve_http(method, target, version, headers, reader, writer)

    async def _read_headers(
        self, reader: asyncio.StreamReader
    ) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        total = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=_HEADER_TIMEOUT)
            if not line:
                raise ProxyError("client closed the connection mid-headers")
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                raise ProxyError("request headers too large")
            if line in (b"\r\n", b"\n"):
                return headers
            text = line.decode("latin-1").rstrip("\r\n")
            if ":" not in text:
                raise ProxyError(f"malformed header line: {text!r}")
            name, value = text.split(":", 1)
            headers.append((name.strip(), value.strip()))

    async def _serve_connect(
        self, authority: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.stats.connects += 1
        try:
            host, port = split_authority(authority)
            upstream_reader, upstream_writer = await self._open_upstream(
                host, port, retries=False
            )
        except ProxyError as err:
            await self._fail(writer, 502, str(err))
            return
        except Exception as err:
            await self._fail(writer, 502, f"unexpected tunnel error: {err}")
            return

        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await self._drain(writer)
        await self._relay(reader, writer, upstream_reader, upstream_writer)

    async def _serve_http(
        self,
        method: str,
        target: str,
        version: str,
        headers: list[tuple[str, str]],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Parse the absolute request URI emitted for proxied requests.
        if target.startswith("http://") or target.startswith("https://"):
            parts = urlsplit(target)
            if parts.scheme != "http":
                # Plain-HTTP requests only; TLS is handled via CONNECT.
                await self._fail(writer, 501, f"unsupported scheme {parts.scheme}")
                return
            host = parts.hostname or ""
            port = parts.port or 80
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            host_header = parts.netloc
        else:
            host_header = ""
            for name, value in headers:
                if name.lower() == "host":
                    host_header = value
                    break
            if not host_header:
                await self._fail(writer, 400, "missing Host header")
                return
            host, port = split_authority(host_header, default_port=80)
            path = target
        if not host:
            await self._fail(writer, 400, "missing host")
            return

        try:
            upstream_reader, upstream_writer = await self._open_upstream(
                host, port, retries=method.upper() in ("GET", "HEAD")
            )
        except ProxyError as err:
            await self._fail(writer, 502, str(err))
            return
        except Exception as err:
            await self._fail(writer, 502, f"unexpected upstream error: {err}")
            return

        lines = [f"{method} {path} {version or 'HTTP/1.1'}"]
        saw_host = False
        for name, value in headers:
            lower = name.lower()
            if lower in (
                "proxy-authorization",
                "proxy-connection",
                "connection",
                "keep-alive",
            ):
                continue
            if lower == "host":
                saw_host = True
            lines.append(f"{name}: {value}")
        if not saw_host:
            lines.append(f"Host: {host_header}")
        lines.append("Connection: close")

        payload = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        self.stats.bytes_out += len(payload)
        upstream_writer.write(payload)
        await self._drain(upstream_writer)
        await self._relay(reader, writer, upstream_reader, upstream_writer)

    async def _open_upstream(
        self, host: str, port: int, *, retries: bool
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect through the upstream, retrying transient failures.

        Only GET/HEAD are retried (safe methods).  Retries cover the proxy
        handshake; once bytes are flowing a failure is reported as-is.
        """
        attempts = 1 + (int(self.config.retries) if retries else 0)
        last_error: ProxyError | None = None
        for attempt in range(attempts):
            try:
                return await self._connector.connect(host, port)
            except ProxyError as err:
                last_error = err
                if attempt + 1 >= attempts:
                    break
                await asyncio.sleep(min(0.3 * (attempt + 1), 1.5))
        assert last_error is not None
        raise last_error

    # -- relaying ----------------------------------------------------------
    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def pump(
            source: asyncio.StreamReader,
            destination: asyncio.StreamWriter,
            counter: str,
        ) -> None:
            try:
                while not self._stopping:
                    data = await source.read(_READ_CHUNK)
                    if not data:
                        break
                    setattr(
                        self.stats, counter, getattr(self.stats, counter) + len(data)
                    )
                    destination.write(data)
                    await destination.drain()
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    destination.write_eof()

        try:
            await asyncio.gather(
                pump(client_reader, upstream_writer, "bytes_out"),
                pump(upstream_reader, client_writer, "bytes_in"),
            )
        finally:
            await _close_writer(upstream_writer)

    @staticmethod
    async def _drain(writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, OSError):
            await writer.drain()

    async def _send_error(
        self, writer: asyncio.StreamWriter, code: int, message: str
    ) -> None:
        body = message.encode("utf-8", "replace")
        reason = {400: "Bad Request", 501: "Not Implemented", 502: "Bad Gateway"}.get(
            code, "Error"
        )
        writer.write(
            (
                f"HTTP/1.1 {code} {reason}\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1")
            + body
        )
        await self._drain(writer)

    async def _fail(
        self, writer: asyncio.StreamWriter, code: int, message: str
    ) -> None:
        self.stats.errors += 1
        self.stats.last_error = message
        _LOGGER.debug("proxy bridge: %s", message)
        await self._send_error(writer, code, message)


# ---------------------------------------------------------------------------
# aiohttp routing patch (shared by every copy of this module in the process)
# ---------------------------------------------------------------------------

_PATCH_FLAG = "_proxy_bridge_patch"
_ROUTES_ATTR = "_proxy_bridge_routes"
_ORIGINAL_ATTR = "_proxy_bridge_original"
_RETRYABLE = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    TimeoutError,
    ConnectionError,
    OSError,
)


@dataclass(frozen=True, slots=True)
class RouteRule:
    """One routing rule: hosts -> loopback bridge."""

    name: str
    hosts: tuple[str, ...]
    upstream: str
    retries: int = 0

    def matches(self, host: str) -> bool:
        """Return True when ``host`` is covered by this rule."""
        return any(host_matches(pattern, host) for pattern in self.hosts)


def _routes_from_patch() -> list[Any]:
    """Return the route list owned by the installed patch (shared)."""
    return getattr(aiohttp.ClientSession._request, _ROUTES_ATTR)


def _match_rule(url: str, kwargs: Mapping[str, Any], routes: list[Any]) -> Any:
    if kwargs.get("proxy"):
        return None
    if not isinstance(url, str):
        url = str(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host or _is_loopback(host):
        return None
    for rule in tuple(routes):
        if rule.matches(host):
            return rule
    return None


def _install_patch() -> None:
    """Patch ``aiohttp.ClientSession._request`` exactly once per process."""
    current = aiohttp.ClientSession._request
    if getattr(current, _PATCH_FLAG, False):
        return

    original = current
    routes: list[Any] = []

    async def _patched_request(self, method, url, **kwargs):
        rule = _match_rule(url, kwargs, routes)
        if rule is None:
            return await original(self, method, url, **kwargs)
        attempts = 1 + (
            int(rule.retries) if str(method).upper() in ("GET", "HEAD") else 0
        )
        last_error: BaseException | None = None
        for attempt in range(attempts):
            call_kwargs = dict(kwargs)
            call_kwargs["proxy"] = rule.upstream
            try:
                return await original(self, method, url, **call_kwargs)
            except _RETRYABLE as err:  # type: ignore[misc]
                last_error = err
                if attempt + 1 >= attempts:
                    break
                await asyncio.sleep(min(0.5 * (attempt + 1), 2.0))
        assert last_error is not None
        raise last_error

    _patched_request.__name__ = "_proxy_bridge_patched_request"
    _patched_request.__qualname__ = "_proxy_bridge_patched_request"
    setattr(_patched_request, _PATCH_FLAG, True)
    setattr(_patched_request, _ROUTES_ATTR, routes)
    setattr(_patched_request, _ORIGINAL_ATTR, original)
    aiohttp.ClientSession._request = _patched_request  # type: ignore[method-assign]


def ensure_patch() -> None:
    """Install the aiohttp patch if it is not installed yet."""
    _install_patch()


def register_route(rule: RouteRule) -> None:
    """Register (or replace) a routing rule in the shared patch."""
    ensure_patch()
    routes = _routes_from_patch()
    routes[:] = [existing for existing in routes if existing.name != rule.name]
    routes.append(rule)


def unregister_route(name: str) -> None:
    """Remove a routing rule by name."""
    if not getattr(aiohttp.ClientSession._request, _PATCH_FLAG, False):
        return
    routes = _routes_from_patch()
    routes[:] = [existing for existing in routes if existing.name != name]


def registered_routes() -> tuple[Any, ...]:
    """Snapshot of the currently routed rules (all integrations)."""
    if not getattr(aiohttp.ClientSession._request, _PATCH_FLAG, False):
        return ()
    return tuple(_routes_from_patch())


def unpatch() -> None:
    """Restore the original ``aiohttp`` implementation (used by tests)."""
    current = aiohttp.ClientSession._request
    if not getattr(current, _PATCH_FLAG, False):
        return
    original = getattr(current, _ORIGINAL_ATTR)
    aiohttp.ClientSession._request = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Runtime wrapper
# ---------------------------------------------------------------------------


class ProxyRuntime:
    """One configured bridge: listener + route + lifecycle."""

    def __init__(
        self, config: BridgeConfig, rule_id: str, *, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        self.config = config
        self.rule_id = rule_id
        self.bridge = LocalBridge(config, timeout=timeout)
        self._started = False

    async def async_start(self) -> None:
        """Start the bridge and route matching hosts through it."""
        if self._started:
            return
        await self.bridge.async_start()
        register_route(
            RouteRule(
                name=self.rule_id,
                hosts=self.config.hosts,
                upstream=self.bridge.url,
                retries=self.config.retries,
            )
        )
        self._started = True

    async def async_stop(self) -> None:
        """Stop routing and release the listener."""
        if not self._started:
            return
        self._started = False
        unregister_route(self.rule_id)
        await self.bridge.async_stop()

    @property
    def started(self) -> bool:
        """Whether the runtime is currently active."""
        return self._started

    def info(self) -> dict[str, Any]:
        """Diagnostics-friendly state snapshot (no secrets)."""
        return {
            "name": self.config.name,
            "started": self._started,
            "listen": self.bridge.url if self._started else None,
            "upstream": self.config.proxy.redacted(),
            "upstream_details": self.config.proxy.as_dict(),
            "hosts": list(self.config.hosts),
            "retries": self.config.retries,
            "stats": self.bridge.stats.as_dict(),
        }


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------


async def async_probe(
    spec: ProxySpec, targets: Iterable[str], *, timeout: float = 20.0
) -> str | None:
    """Check that ``spec`` can reach one of ``targets``.

    Returns ``None`` on success, otherwise a human readable error.  A bare
    HTTP response (even 403/404) counts as success: the point is reachability.
    """
    hosts = [host for host in targets if host and "*" not in host and "?" not in host]
    if not hosts:
        return "no concrete host to test"
    bridge = LocalBridge(
        BridgeConfig("probe", spec, tuple(hosts[:1]), 0), timeout=DEFAULT_TIMEOUT
    )
    await bridge.async_start()
    client_timeout = aiohttp.ClientTimeout(
        total=timeout, connect=DEFAULT_TIMEOUT, sock_read=timeout
    )
    try:
        connector = aiohttp.TCPConnector(force_close=True, enable_cleanup_closed=True)
        async with aiohttp.ClientSession(
            timeout=client_timeout, connector=connector
        ) as session:
            for host in hosts[:1]:
                try:
                    async with session.get(
                        f"https://{host}/", proxy=bridge.url, ssl=False
                    ) as response:
                        if response.status >= 400:
                            body = (await response.text()).strip()
                            detail = f"HTTP {response.status}"
                            if body:
                                detail = f"{detail}: {body}"
                            return f"{host}: {detail}"
                        return None
                except (aiohttp.ClientError, TimeoutError, OSError) as err:
                    detail = bridge.stats.last_error
                    if detail:
                        return f"{host}: {err} ({detail})"
                    return f"{host}: {err}"
    finally:
        await bridge.async_stop()
    return None

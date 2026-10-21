"""Shared fakes for the Proxy Bridge test suite.

Everything here is plain asyncio/aiohttp: no Home Assistant required, so the
transport layer can be tested on any machine.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import socket
import ssl
from collections.abc import Awaitable

# ---------------------------------------------------------------------------
# Relay helper
# ---------------------------------------------------------------------------


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes until EOF, then half-close the destination."""
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.write_eof()


async def relay(
    client: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
) -> None:
    """Relay a duplex stream pair until both directions finish."""
    await asyncio.gather(pipe(client[0], upstream[1]), pipe(upstream[0], client[1]))
    upstream[1].close()
    with contextlib.suppress(Exception):
        await upstream[1].wait_closed()


async def close_writer(writer: asyncio.StreamWriter) -> None:
    """Close a writer, swallowing errors."""
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


# ---------------------------------------------------------------------------
# Origin server: a tiny plain-HTTP endpoint
# ---------------------------------------------------------------------------

ORIGIN_BODY = b"hello from the origin"


class OriginServer:
    """Plain HTTP origin that answers every request with a fixed body."""

    def __init__(self) -> None:
        self.port: int | None = None
        self.hits = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.hits += 1
        try:
            await reader.readline()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                + f"Content-Length: {len(ORIGIN_BODY)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + ORIGIN_BODY
            )
            await writer.drain()
        finally:
            await close_writer(writer)


# ---------------------------------------------------------------------------
# HTTP proxy: CONNECT tunnel with optional basic auth and scripted failures
# ---------------------------------------------------------------------------


def make_server_tls_context() -> ssl.SSLContext:
    """Create a throwaway self-signed server context for tests."""
    import datetime
    import tempfile
    from pathlib import Path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    directory = Path(tempfile.mkdtemp(prefix="proxy-bridge-tls-"))
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


class TlsOriginServer:
    """HTTPS origin (self-signed) used by the connection-test cases."""

    def __init__(self) -> None:
        self.port: int | None = None
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=make_server_tls_context()
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        try:
            await reader.readline()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        finally:
            await close_writer(writer)


class FakeHttpProxy:
    """A CONNECT-capable HTTP proxy."""

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        fail_connects: int = 0,
        mapping: dict[str, str] | None = None,
        secure: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.fail_connects = fail_connects
        self.mapping = mapping or {}
        self.port: int | None = None
        self.connects: list[str] = []
        self.auth_headers: list[str | None] = []
        self.tls_context: object | None = None
        self._server: asyncio.AbstractServer | None = None
        self._secure = secure

    async def start(self) -> None:
        ssl_context = make_server_tls_context() if self._secure else None
        if ssl_context is not None:
            self.tls_context = ssl_context
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=ssl_context
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            method, target, _version = line.decode("latin-1").split()
            headers: dict[str, str] = {}
            while True:
                raw = await reader.readline()
                if raw in (b"\r\n", b"\n", b""):
                    break
                name, value = raw.decode("latin-1").split(":", 1)
                headers[name.strip().lower()] = value.strip()
            self.auth_headers.append(headers.get("proxy-authorization"))
            if method.upper() != "CONNECT":
                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await writer.drain()
                return
            if self.fail_connects > 0:
                self.fail_connects -= 1
                await close_writer(writer)
                return
            if self.username is not None:
                expected = (
                    "Basic "
                    + base64.b64encode(
                        f"{self.username}:{self.password or ''}".encode()
                    ).decode()
                )
                if headers.get("proxy-authorization") != expected:
                    writer.write(
                        b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                        b"Content-Length: 0\r\n\r\n"
                    )
                    await writer.drain()
                    return
            self.connects.append(target)
            host, _, port_text = target.rpartition(":")
            host = host.strip("[]")
            port = int(port_text)
            mapped = self.mapping.get(host)
            if mapped and ":" in mapped:
                mapped_host, _, mapped_port = mapped.rpartition(":")
                host, port = mapped_host, int(mapped_port)
            elif mapped:
                host = mapped
            upstream = await asyncio.open_connection(host, port)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await relay((reader, writer), upstream)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            await close_writer(writer)


# ---------------------------------------------------------------------------
# SOCKS5 server
# ---------------------------------------------------------------------------


class FakeSocks5Proxy:
    """RFC 1928 SOCKS5 server with optional username/password auth."""

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.mapping = mapping or {}
        self.port: int | None = None
        self.targets: list[tuple[str, int, int]] = []
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            version, method_count = await reader.readexactly(2)
            if version != 5:
                return
            methods = await reader.readexactly(method_count)
            want_auth = self.username is not None
            chosen = 0x02 if want_auth and 0x02 in methods else 0x00
            if want_auth and 0x02 not in methods:
                writer.write(bytes([5, 0xFF]))
                await writer.drain()
                return
            writer.write(bytes([5, chosen]))
            await writer.drain()
            if chosen == 0x02:
                sub_version = (await reader.readexactly(1))[0]
                user_length = (await reader.readexactly(1))[0]
                user = (await reader.readexactly(user_length)).decode()
                pass_length = (await reader.readexactly(1))[0]
                password = (await reader.readexactly(pass_length)).decode()
                ok = (
                    sub_version == 1
                    and user == self.username
                    and password == (self.password or "")
                )
                writer.write(bytes([1, 0 if ok else 1]))
                await writer.drain()
                if not ok:
                    return

            version, command, _reserved, address_type = await reader.readexactly(4)
            if address_type == 0x01:
                host = socket.inet_ntoa(await reader.readexactly(4))
            elif address_type == 0x03:
                length = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(length)).decode()
            elif address_type == 0x04:
                host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
            else:
                return
            port = int.from_bytes(await reader.readexactly(2), "big")
            self.targets.append((address_type, port, command))
            mapped = self.mapping.get(host, host)
            target_port = port
            if mapped != host and ":" in mapped:
                mapped, raw_port = mapped.rsplit(":", 1)
                target_port = int(raw_port)
            upstream = await asyncio.open_connection(mapped, target_port)
            writer.write(bytes([5, 0, 0, 1, 0, 0, 0, 0, 0, 0]))
            await writer.drain()
            await relay((reader, writer), upstream)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            await close_writer(writer)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


async def run_with_timeout(coro: Awaitable[None], timeout: float = 15.0) -> None:
    """Run a coroutine with a hard timeout so a hang fails the test."""
    await asyncio.wait_for(coro, timeout=timeout)


def free_port() -> int:
    """Return a currently free TCP port on loopback."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def DEFAULT_MAPPING() -> dict[str, str]:
    """Map the fake host to port 80 by default."""
    return {"origin.test": "127.0.0.1"}

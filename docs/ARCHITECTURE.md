# Architecture

## Goal

Let a Home Assistant installation reach a handful of blocked host names through
an upstream proxy, without patching HACS, without changing every integration and
without an HTTP(S) man-in-the-middle.

## Non-goals

- A general-purpose system proxy (Supervisor, add-ons, OS updates, pip).
- Content filtering, ad blocking, HTML rewriting, header injection.
- Transparent interception of TLS (we tunnel; we never decrypt).
- Covering non-aiohttp libraries (`requests`, `urllib`, raw sockets).

## Pieces

| File | Responsibility |
| --- | --- |
| `proxy_core.py` | Transport: local loopback bridge, upstream connectors (HTTP/HTTPS/SOCKS5), the aiohttp patch, retries, probe. No Home Assistant imports. |
| `__init__.py` | Config entries, YAML import, the `probe` service, the bootstrap repair issue. |
| `config_flow.py` | Two-step wizard, options, reconfigure, import. |
| `diagnostics.py`, `system_health.py` | Observability. |
| `bootstrap/` | A second integration with the same `proxy_core.py`, installable by hand. |

`proxy_core.py` is copied **byte for byte** into both integrations so either one
can work alone. `scripts/validate.py` and `tests/test_packaging.py` fail if the
copies drift.

## Request path

```
caller: await session.get("https://raw.githubusercontent.com/...")
   │
   ├─ patched ClientSession._request
   │     host matches a RouteRule?  caller passed proxy=?  scheme http(s)?
   │        no  → call the original _request untouched
   │        yes → call the original _request with proxy="http://127.0.0.1:<port>"
   │
   ├─ aiohttp talks to the loopback bridge
   │     https → CONNECT host:443
   │     http  → GET http://host/... (absolute form)
   │
   ├─ LocalBridge
   │     CONNECT  → UpstreamConnector.connect(host, port) → 200 → relay bytes
   │     plain    → connect, rewrite absolute URI to origin form, add
   │                "Connection: close", relay bytes
   │
   └─ upstream proxy (HTTP CONNECT / HTTPS CONNECT / SOCKS5 connect)
```

Because aiohttp only ever sees a normal HTTP proxy, TLS validation, cookies and
keep-alive for HTTPS stay exactly as they were: the bridge is a byte pipe.

## The patch contract

- Installed lazily on `aiohttp.ClientSession._request` and only once per
  process. The route table lives *on the patched function*, so two copies of
  `proxy_core` (managed + bootstrap) share the same table instead of stacking
  two patches.
- Rules are matched by host only; the path is irrelevant.
- A caller-provided `proxy=` always wins; the patch never rewrites it.
- Loopback hosts (`localhost`, `127.0.0.0/8`, `::1`) are never routed.
- No route → the original method is called with the original arguments.
- Retries: `1 + retries` attempts for GET/HEAD only, on connection errors,
  server disconnects, timeouts and `OSError`; backoff caps at 2 s.

The patch stays installed for the lifetime of the process. Unloading every entry
empties the route table (transparent pass-through); restart to recover the
original method — which is also why removing the integration needs a restart.

## Local bridge details

- Binds `127.0.0.1:0` (ephemeral port), one listener per config entry.
- Header parsing is bounded (64 KiB, 30 s per header line) so a stuck client
  cannot pin the listener.
- Plain HTTP requests are forwarded once and closed (`Connection: close`) to
  keep framing trivial; HTTPS tunnels are long-lived and support keep-alive.
- `Server.wait_closed()` is called *after* cancelling handlers: since Python
  3.12 that method waits for active connections, so the naive order deadlocks.
- Stream closes are bounded by a 2 s timeout and then aborted, because TLS
  shutdown can wait forever on a half-open peer.

## Upstream connectors

| Type | Handshake | Auth |
| --- | --- | --- |
| `http` | `CONNECT host:port` | `Proxy-Authorization: Basic` |
| `https` | TLS to the proxy, then `CONNECT` | same; `insecure` disables verification |
| `socks5h` | RFC 1928, ATYP=domain (proxy resolves) | RFC 1929 |
| `socks5` | RFC 1928, ATYP=IP after local `getaddrinfo` | RFC 1929 |

All handshakes are wrapped in timeouts (15 s) and translated into human-readable
`ProxyError`s (`407`, refused CONNECT, wrong SOCKS credentials, unreachable
proxy). For plain HTTP these become a `502` response; for HTTPS they surface as
aiohttp's proxy connection error.

## Config entries

- `entry.data` holds the initial settings; `entry.options` holds later edits.
- `effective_options(entry)` merges them (options win), so partial migrations
  never drop keys.
- Disabled entries stay loaded with `runtime_data = None`.
- Multiple entries are allowed: each owns a listener and a route rule. First
  registered rule wins on overlap, which makes "specific override before
  catch-all" work by creation order.
- `name` doubles as the unique id (`slugify`), so duplicates are rejected with
  `already_configured`.

## Import paths

1. **YAML** (`proxy_bridge:` blocks) → `async_step_import`, URL parsed with
   `parse_proxy_string`.
2. **Bootstrap** (`proxy_bridge_bootstrap` entries) → menu on the first step,
   settings prefilled from the bootstrap entry's effective options, marker
   `imported_from` stored for diagnostics.
3. **Old `proxy_shim`**: paste its `proxy`/`hosts`/`retries` into the YAML form
   above; the same keys are accepted.

## Observability

- `INFO` on setup: version, route count, redacted upstream, listen port.
- `WARNING` when a connectivity test is skipped.
- `DEBUG` per failed tunnel (message only, never credentials).
- Diagnostics: redacted options plus live counters (`requests`, `connects`,
  `errors`, bytes in/out, last error).
- System Health: active rules and entry count.
- Repair issue while the bootstrap integration is still installed.

## Home Assistant compatibility

The integration only touches stable, long-lived APIs: config entries, config
flows, selectors, the issue registry, dispatcher signals, services and the
`system_health` platform protocol. Two version-sensitive details are handled
deliberately:

- `SystemHealthRegistration` is imported from
  `homeassistant.components.system_health` (its current home in both 2025.1 and
  2026.10); the older `homeassistant.helpers.system_health` alias is not used.
- `SupportsResponse` is imported from `homeassistant.core` (its home since
  2025.2).

The engine itself (`proxy_core.py`) depends only on `aiohttp` and `asyncio`, so
it does not track Home Assistant releases at all.

The test suite runs against `pytest-homeassistant-custom-component` (which pins
Home Assistant 2025.1.4), and every import above was also checked against the
Home Assistant 2026.10.2 source tree before release.

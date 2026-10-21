# Proxy Bridge

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-41BDF5.svg)](https://www.home-assistant.io)
[![Built with AI](https://img.shields.io/badge/built%20with-AI-8A2BE2.svg)](#authors-and-how-it-was-built)

Route **selected hosts** through an HTTP/HTTPS/SOCKS5 proxy from inside Home
Assistant — without touching Home Assistant Core, and without a TLS
man-in-the-middle.

Home Assistant never reads `HTTP_PROXY`/`HTTPS_PROXY`, and HACS downloads
everything straight from GitHub. On networks where `github.com`,
`raw.githubusercontent.com` or `codeload.github.com` are blocked (or flooded
with connection resets), that means you cannot install or update integrations
at all — including the helper that would fix the problem.

Proxy Bridge is that helper. It is deliberately **not** HACS-only: it hooks
`aiohttp.ClientSession` once, and every aiohttp request Home Assistant makes
(Core, HACS, custom integrations) is matched against your rules.

- **Zero third-party dependencies** — pure aiohttp + asyncio.
- **SOCKS5 / SOCKS5H** with RFC 1929 username/password authentication.
- **HTTP and HTTPS proxies**, with optional Basic authentication and the
  option to skip upstream certificate verification (self-signed proxies).
- **Visual configuration** in the UI, including a connectivity test.
- **English and Simplified Chinese** out of the box, with a documented path
  for more languages.
- **Bootstrap package** for the chicken-and-egg case: a second, differently
  named integration you can copy in by hand before any proxy exists, which the
  managed integration can then import settings from.
- **Diagnostics, system health and a `probe` service** for troubleshooting.

## Authors and how it was built

**Proxy Bridge is AI-built software.** It was designed and implemented jointly
by **Setsuna-Kitahara** and the AI coding agent **DeepSeek Harness** (running
the `deepseek-flash` model). Architecture, the aiohttp patch, the dependency-free
SOCKS5 client, the config flow, the translation pipeline and the test suite were
written in that collaboration; the behaviour is pinned down by the automated
tests under `tests/` and validated against Home Assistant 2025.1.4 (pinned by
`pytest-homeassistant-custom-component`) plus a source review of 2026.10.2.

The AI involvement is disclosed on purpose: judge the project by its tests,
its code and its issue tracker, not by who (or what) typed it. Bug reports and
patches from humans and agents are equally welcome.

Released under the [MIT license](LICENSE) — copyright 2026 Setsuna-Kitahara and
DeepSeek Harness.

## How it works

```
aiohttp.ClientSession._request (patched once, process wide)
        │  host matches a rule and no explicit proxy was given?
        ▼
http://127.0.0.1:<random port>   ← local bridge, one per config entry
        │  CONNECT / origin-form forwarding
        ▼
HTTP · HTTPS · SOCKS5 · SOCKS5H  →  upstream proxy  →  the internet
```

The patch only *adds* a `proxy=` argument and never removes or overrides an
explicit `proxy=` set by a caller, so integrations that already use a proxy
keep working. HTTPS traffic is tunnelled (`CONNECT`) end to end — Proxy Bridge
never sees or decrypts TLS content.

## Requirements

- Home Assistant **2024.12** or newer.
- A proxy server that the Home Assistant host can reach. Confirm the Home
  Assistant host can connect to the proxy you intend to use.
- HACS is optional — the release archives contain every file.

## Verifying your download

Release archives are published together with `SHA256SUMS` on the
[releases page](https://github.com/Setsuna-Kitahara/proxy-bridge/releases) and
inside `dist/` in a checkout. If the file reached you through a mirror, a proxy
cache or a chat app, verify it before copying anything into `/config`:

```bash
sha256sum -c SHA256SUMS                              # Linux
shasum -a 256 proxy_bridge_bootstrap-1.0.0.zip       # macOS
Get-FileHash .\proxy_bridge_bootstrap-1.0.0.zip -Algorithm SHA256   # PowerShell
```

<!-- sha256:begin -->
| Archive | SHA-256 |
| --- | --- |
| `proxy_bridge-1.0.0.zip` | `00764afe0ccc6c70f8a67340d83f025f515f45ebb813c4da36b0a6e4bd759270` |
| `proxy_bridge_bootstrap-1.0.0.zip` | `00e6dd47ba8958f2fe9e648edff777ca2957ce2781475f0c36b614770c31bc1b` |
<!-- sha256:end -->

A mismatch means the bytes are not the ones built from this repository: do not
install them. The project does not ship code signatures yet, so these checksums
— published here and in the release — are the integrity signal.

## Installation

### From HACS (once GitHub is reachable)

1. HACS → **Custom repositories** → add
   `https://github.com/Setsuna-Kitahara/proxy-bridge` as an *Integration*.
2. Install **Proxy Bridge**, restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Proxy Bridge**.
4. Enter your proxy details, pick the hosts, run the test, save.

### When GitHub is *not* reachable (bootstrap)

Use the second package, `proxy_bridge_bootstrap`. It has a different domain on
purpose: it is installed by hand, and the managed integration later imports its
settings and reminds you to remove it. See
[docs/BOOTSTRAP.md](docs/BOOTSTRAP.md).

## Configuration

| Field | Description |
| --- | --- |
| Name | Entry name, also the unique id. |
| Proxy type | `http`, `https`, `socks5` (resolve locally) or `socks5h` (let the proxy resolve; recommended). |
| Host / Port | Address of the upstream proxy. Use a LAN address such as `192.168.1.10`, not `127.0.0.1`, unless the proxy runs inside the Home Assistant container. |
| Username / Password | Optional. Sent as `Proxy-Authorization: Basic` for HTTP(S) proxies, RFC 1929 for SOCKS5. |
| Verify upstream TLS | Turn off only for HTTPS proxies with self-signed certificates. |
| Hosts | Presets plus any host you type. Exact names, `*.example.com` wildcards or glob patterns. |
| Retries | Extra attempts for **GET/HEAD** on connection errors and timeouts (0–10). |
| Enabled | Keep the entry but route nothing. |
| Test connection | Performs a real request through the new settings; uncheck to save without network access. |

Default hosts cover the GitHub download path plus HACS metadata:

```
raw.githubusercontent.com  objects.githubusercontent.com  codeload.github.com
github.com                 api.github.com                 gist.githubusercontent.com
release-assets.githubusercontent.com  data-v2.hacs.xyz
```

Because HACS pulls release archives from `codeload.github.com` but falls back
to per-file downloads from `raw.githubusercontent.com`, both must be routed.

> ⚠️ **Proxy credentials are stored unencrypted.** The username and password you
> enter are kept by Home Assistant in the config entry (`.storage/core.config_entries`)
> as plain text. Home Assistant does not encrypt integration secrets, and Proxy Bridge
> does not add encryption of its own — so anyone who can read the Home Assistant
> configuration directory, or a backup or snapshot of it, can read those credentials.
> Prefer a dedicated proxy account, and do not reuse a password that matters elsewhere.
> Diagnostics redact the password, but that only covers the diagnostics download.

### YAML (optional)

`configuration.yaml` is not required — the UI is the normal way. If you prefer
YAML, add entries once and let the import flow create config entries:

```yaml
proxy_bridge:
  - proxy: "socks5h://user:secret@192.168.1.10:1080"
    name: Home proxy
    hosts: [github.com, raw.githubusercontent.com, "*.githubusercontent.com"]
    retries: 2
    enabled: true
```

The same file format that the old `proxy_shim` experiment used can be pasted
here: `proxy`, `hosts` and `retries` are understood.

### Service

```yaml
service: proxy_bridge.probe
data:
  proxy: "socks5h://user:secret@192.168.1.10:1080"
  hosts: [raw.githubusercontent.com]
response_variable: probe
```

`probe` opens a temporary bridge, requests `https://<host>/` through it and
returns `{ok, error, hosts}`. Useful when the UI cannot be reached, or to test
a candidate proxy before saving it.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Test fails with `407` | Proxy requires credentials — fill username/password. |
| Test fails with `certificate` errors | HTTPS proxy with a self-signed certificate — disable *Verify upstream TLS*. |
| Test succeeds but HACS still fails | The blocked host is not in *Hosts*. Add it and watch the log for `routing ... through ...`. |
| Nothing is routed | An entry is disabled, or the caller passed its own `proxy=` (e.g. another proxy integration). Do not run alongside `hacs-proxy`. |
| First requests after startup fail | The integration loads after some integrations have already started; HACS refreshes every 30–60 minutes, or reload the integration. |

Enable debug logging to see every decision:

```yaml
logger:
  logs:
    custom_components.proxy_bridge: debug
```

Diagnostics (Settings → Devices & Services → Proxy Bridge → ⋯ → Download
diagnostics) contain the effective settings with the password replaced by a
boolean, the live listen address, routes and counters. Passwords are never
written to logs or diagnostics.

## Prior art

- [`ryanh7/hacs-proxy`](https://github.com/ryanh7/hacs-proxy) (2022, ~123★) —
  patches HACS internals (`hacs.session`, `hacs.github._session`) and supports
  a single HTTP proxy URL. It has no SOCKS5, no proxy authentication, no host
  selection, no i18n framework and no bootstrap story, and it breaks whenever
  HACS changes its session handling. Proxy Bridge patches the shared aiohttp
  layer instead, so it does not depend on HACS internals.
- Home Assistant Core: no global proxy support (`HTTP_PROXY` is ignored); the
  upcoming `marketplace` integration in the dev branch has no proxy option
  either.
- HACS 2.x: no proxy setting anywhere in the codebase.
- [`dermotduffy/hass-web-proxy-integration`](https://github.com/dermotduffy/hass-web-proxy-integration)
  is a *reverse* web proxy for media URLs — unrelated.

## Limitations

- Covers **aiohttp** traffic only. `requests`/`urllib`/raw sockets are not
  intercepted (Home Assistant Core and HACS are aiohttp-based).
- No TLS interception by design: `.onion`-style filtering, HTML rewriting and
  header injection are out of scope.
- The local bridge listens on `127.0.0.1` without authentication — any local
  process can use it as a proxy while an entry is active.
- Proxy credentials are stored by Home Assistant in `.storage` in plain text,
  exactly like other integration secrets.
- Retries apply to GET/HEAD only; POST/PUT are never replayed.

## Contributing

- Translations: see [docs/TRANSLATING.md](docs/TRANSLATING.md).
- Architecture and design decisions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Security model: [docs/SECURITY.md](docs/SECURITY.md).
- Release steps: [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check .
python -m pytest -q
python scripts/validate.py
python scripts/build_dist.py
```

## License

MIT © 2026 Setsuna-Kitahara and DeepSeek Harness — see [LICENSE](LICENSE).

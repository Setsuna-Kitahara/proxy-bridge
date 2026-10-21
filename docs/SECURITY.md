# Security model

Proxy Bridge is a local forwarding component, not a security boundary. This
page states what it protects, what it does not, and where credentials end up.

## What it does

- Binds a small HTTP proxy on `127.0.0.1` (an ephemeral port per config entry).
- Injects `proxy=http://127.0.0.1:<port>` into `aiohttp` requests whose host
  matches your route, and only when the caller did not pass its own `proxy=`.
- For `https://` requests it opens a `CONNECT` tunnel: the TLS handshake and
  all bytes are end-to-end between Home Assistant and the destination.
  Proxy Bridge cannot read them.
- For plain `http://` requests it rewrites the request line to origin-form and
  forwards with `Connection: close`.
- Authenticates to the upstream proxy when configured: HTTP(S) proxy →
  `Proxy-Authorization: Basic`; SOCKS5 → RFC 1929 username/password.

## Credentials

Proxy usernames and passwords are stored in the Home Assistant config entry
(`/config/.storage/core.config_entries`), **in clear text**, exactly like API
keys of other integrations. Anyone with filesystem or backup access to
`/config` can read them.

Mitigations inside the integration:

- the password is never logged;
- `diagnostics.py` replaces it with `password_set: true`;
- the options flow never echoes the stored password back into the form it
  renders (the UI shows a "leave blank to keep" placeholder instead);
- `ProxySpec.redacted()` is used everywhere a config object can reach an error
  message or a log line;
- the `probe` service accepts a proxy inline but does not persist it.

There is no encryption at rest. If that is unacceptable, use a proxy that
accepts a source-IP allow-list instead of credentials, or terminate the proxy
on the same LAN and restrict it by firewall.

## The loopback listener

The listener is unauthenticated and reachable by any process that can reach
`127.0.0.1` inside the Home Assistant container. It is a convenience, not a
boundary: another add-on running on the same host could use it as an open
proxy to your upstream. On a single-user Home Assistant OS install this is
usually acceptable; in shared or multi-tenant setups, prefer an allow-listed
upstream and keep the routes narrow.

The listener never accepts connections from outside the loopback interface.

## Upstream visibility

Your upstream proxy sees, by design, the destination host and port for every
routed request. For plain HTTP it also sees the full URL and body. For HTTPS it
sees only the hostname (CONNECT target). With `socks5h` the *proxy* resolves
DNS; with `socks5` Home Assistant resolves it locally first. Choose based on
whether you trust local DNS to know the hostname.

## What it does not protect against

- TLS interception, content filtering, header rewriting — out of scope by
  design (no MITM).
- Malicious code in an integration: anything running in the Home Assistant
  process can already read `.storage` directly.
- SSRF: the domains list is a routing rule, not an allow-list for requests.
  The integration does not make requests to arbitrary URLs on its own; the
  `probe` service only contacts `https://<host>/` for hosts you pass.
- Supply chain of the proxy itself. You are trusting the upstream proxy
  operator with your traffic metadata.

## Trust of the bootstrap copy

The bootstrap zip is downloaded from a release asset and installed by hand.
Verify it against `SHA256SUMS` from the same release if your threat model
includes a compromised release page. Both integration packages are built from
the same source in `scripts/build_dist.py`, and `validate.py` refuses to build
a release with a mismatched `proxy_core.py`.

## Reporting

Open a private security advisory on the GitHub repository rather than a public
issue for anything remotely exploitable. There is no bounty.

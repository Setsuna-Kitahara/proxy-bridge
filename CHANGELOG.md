# Changelog

All notable changes to Proxy Bridge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`scripts/build_dist.py` copies the section of the version being built into the
release notes, so add the entry before building a release.

## 1.0.1 - 2026-10-22

### Fixed

- The `proxy_bridge.probe` service raised `TypeError: ProxySpec.__init__() got
  an unexpected keyword argument 'proxy_type'` and returned HTTP 500, because
  `parse_proxy_string()` returns config-entry option keys while `ProxySpec`
  takes dataclass field names. Both now go through the new
  `proxy_spec_from_options()` helper, so the probe works again.
- Added a regression test for the service and for the option-key mapping.

### Added

- `services.yaml` describing `proxy_bridge.probe` (name, fields, examples), so
  the service shows up with documentation in Developer Tools.

## 1.0.0 - 2026-10-21

### Added

- Managed integration (`proxy_bridge`) and bootstrap integration
  (`proxy_bridge_bootstrap`) sharing one byte-identical `proxy_core.py`.
- HTTP, HTTPS and SOCKS5/SOCKS5H upstreams, optional username/password
  authentication, per-domain routing, retries and a connection test.
- English and Simplified Chinese UI strings plus the translation scaffolding
  (`scripts/i18n.py`, `i18n/languages.json`, `docs/TRANSLATING.md`).
- YAML import, options/reconfigure flows, diagnostics, system health, brand
  assets, `probe` service, tests, packaging script and CI.
- README (EN/ZH), architecture, bootstrap, translation, security and release
  documentation.

# The bootstrap copy

## Why two integrations?

The chicken-and-egg problem: you need a proxy to reach GitHub, and you need
GitHub to install the thing that gives you a proxy.

Proxy Bridge therefore ships **two integrations with different names**:

| | Domain | Shown in the UI | Installed from |
| --- | --- | --- | --- |
| Managed | `proxy_bridge` | Proxy Bridge | HACS, or the release zip |
| Bootstrap | `proxy_bridge_bootstrap` | Proxy Bridge (Bootstrap) | a zip you copy in by hand |

The bootstrap copy is a normal integration that shares the exact same engine
(`proxy_core.py`, byte-identical in both packages). It has no YAML import and
no "import from bootstrap" step, because there is nothing to import from.

Once the proxy works, you install the managed copy, and the managed copy reads
the bootstrap settings directly — you do not have to type anything twice.

## Getting the file without GitHub

The bootstrap archive is attached to every release as
`proxy_bridge_bootstrap-<version>.zip`. Any of these work:

- download it on a phone or another machine and copy it over Samba / Synology
  Drive / a USB stick;
- download it on any machine that can reach GitHub and `scp` it to the host;
- rebuild it locally with `python scripts/build_dist.py` (see the repository).

The zip contains exactly one folder: `custom_components/proxy_bridge_bootstrap/`.

## Installing by hand

Pick whichever route you have:

**Samba / SSH add-on / File editor** — unzip into `/config` so you end up with:

```
/config/custom_components/proxy_bridge_bootstrap/
    __init__.py  config_flow.py  const.py  manifest.json
    proxy_core.py  strings.json  translations/
```

**SSH add-on** (from the Home Assistant host):

```bash
cd /config
curl -L -o /tmp/bootstrap.zip https://github.com/Setsuna-Kitahara/proxy-bridge/releases/latest/download/proxy_bridge_bootstrap-1.0.0.zip
curl -L -o /tmp/SHA256SUMS https://github.com/Setsuna-Kitahara/proxy-bridge/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS   # run from /tmp; must print OK
unzip -o /tmp/bootstrap.zip -d /config
```

**Terminal add-on** with a file already on the host: same `unzip`.

Then **restart Home Assistant** (Developer tools → Restart, or *Settings →
System → Restart*).

## Configuring

*Settings → Devices & services → Add integration →* **Proxy Bridge
(Bootstrap)**. The wizard is two steps, identical to the managed copy:

1. **Connection** — proxy type, host, port, optional username/password,
   optional "verify upstream TLS", and a *Test connection* switch.
2. **Routes** — a name, the domains to route (presets cover the whole GitHub
   download path and the HACS metadata endpoints), retries, and an enabled
   switch.

Save. HACS should now be able to download.

## Moving to the managed copy

1. Install the managed copy the normal way:
   - HACS → *Custom repositories* → add
     `https://github.com/Setsuna-Kitahara/proxy-bridge` (category
     *Integration*), then install **Proxy Bridge**; **or**
   - unzip `proxy_bridge-<version>.zip` into `/config` (it puts
     `custom_components/proxy_bridge/` in place).
2. Restart Home Assistant.
3. *Add integration →* **Proxy Bridge**. Because the bootstrap copy is
   configured, the first screen offers **Import settings from the bootstrap
   copy**. Pick it: name, proxy, credentials and routes are prefilled. Review
   them and save.
4. A repair issue reminds you while both are loaded. Remove the bootstrap copy:
   - *Settings → Devices & services →* **Proxy Bridge (Bootstrap)** → delete;
   - delete `/config/custom_components/proxy_bridge_bootstrap/`;
   - restart.

The managed entry is tagged with `imported_from: proxy_bridge_bootstrap` in its
config entry data, so the repair issue and diagnostics can point this out.

## If GitHub becomes unreachable again

The managed copy keeps working — it does not download anything at runtime. The
only thing that breaks is HACS fetching *new* versions, which is exactly what
the proxy is for. If you ever lose the proxy, the bootstrap copy is still in
your release assets and can be reinstalled the same way.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Integration not listed | The folder must be `/config/custom_components/proxy_bridge_bootstrap/` (not a nested folder from the zip, and not `custom_components/custom_components/`). Restart after copying. |
| `Invalid handler specified` | `manifest.json` and `config_flow.py` must be present; check the folder name equals the domain. |
| Test connection fails but HACS works | The bootstrap copy does not need the test to pass; verify the domains list instead. |
| Both copies configured, traffic duplicated | Entries do not stack: the first matching route wins. Remove the bootstrap copy once migrated. |

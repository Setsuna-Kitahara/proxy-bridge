#!/usr/bin/env python3
"""Build the distributable archives.

    python scripts/build_dist.py             # development archives
    python scripts/build_dist.py --release   # refuses placeholders / -dev versions

Produced in ``dist/``:

* ``proxy_bridge_bootstrap-<version>.zip``  - unzip into ``/config``; the egg
  users install when HACS cannot reach GitHub yet.
* ``proxy_bridge-<version>.zip``            - managed copy for manual installs
  (HACS users should add the repository instead).
* ``SHA256SUMS``                            - checksums for `sha256sum -c`.
* ``RELEASE_NOTES.md``                      - ready-to-paste release body.
* the checksum table inside both READMEs     - refreshed in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGED = ROOT / "custom_components" / "proxy_bridge"
BOOTSTRAP = ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap"
DIST = ROOT / "dist"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
SHA_BEGIN = "<!-- sha256:begin -->"
SHA_END = "<!-- sha256:end -->"
README_FILES = ("README.md", "README.zh-Hans.md")

RELEASE_NOTES = """\
# Proxy Bridge {version}

Route selected hosts through an HTTP/HTTPS/SOCKS5 proxy from inside Home
Assistant. Two archives, same code base:

| Archive | What it is |
| --- | --- |
| `proxy_bridge-{version}.zip` | The managed integration. HACS users should add this repository instead; use the zip only for a manual install. |
| `proxy_bridge_bootstrap-{version}.zip` | The bootstrap integration for the case where GitHub is not reachable yet. Unzip into `/config`, restart, configure, then install the managed copy and import the settings. |

## SHA-256

```
{sums}
```

Verify before installing:

```bash
sha256sum -c SHA256SUMS
```

On macOS: `shasum -a 256 <archive>`. On Windows: `Get-FileHash .\\<archive> -Algorithm SHA256`.

A mismatch means the archive is not the one built from this repository - do
not install it. The
project ships no code signatures yet, so these checksums are the integrity
signal.

## Install (bootstrap)

1. Unzip so you get `/config/custom_components/proxy_bridge_bootstrap/`.
2. Restart Home Assistant.
3. Settings > Devices & services > Add integration > "Proxy Bridge (Bootstrap)".
4. Reach GitHub, add this repository to HACS, install "Proxy Bridge", and
   import the settings from the bootstrap copy.

Full guide: docs/BOOTSTRAP.md
"""

INSTALL_TEXT = """\
Proxy Bridge (Bootstrap) {version}
================================

You are holding the bootstrap copy, which exists because HACS cannot reach
GitHub before a proxy is configured.

1. Unzip this archive into your Home Assistant configuration directory so that
   you end up with  /config/custom_components/proxy_bridge_bootstrap/
2. Restart Home Assistant.
3. Go to Settings > Devices & services > Add integration > "Proxy Bridge
   (Bootstrap)" and enter your proxy.
4. Once GitHub is reachable, add this repository to HACS, install
   "Proxy Bridge", and import the settings from the bootstrap copy.
5. Delete /config/custom_components/proxy_bridge_bootstrap and remove the
   bootstrap entry from Settings > Devices & services.

Files in this archive are only the custom component folder; nothing else in
your configuration is touched.

Before unzipping, compare the SHA-256 of this archive with the one published in
the release notes / SHA256SUMS file. A mismatch means it is not the archive
built by the project maintainers.
"""


def version() -> str:
    """Read the managed integration version."""
    manifest = json.loads((MANAGED / "manifest.json").read_text(encoding="utf-8"))
    return manifest["version"]


def collect(component: Path) -> list[Path]:
    """Return every file that belongs in the archive, sorted."""
    files = []
    for path in sorted(component.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files.append(path)
    return files


def write_zip(
    target: Path,
    component: Path,
    base: Path,
    extra: dict[str, bytes] | None = None,
) -> None:
    """Write a reproducible zip with fixed timestamps."""
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in collect(component):
            relative = path.relative_to(base).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIME)
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
        for name, payload in sorted((extra or {}).items()):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)


def main(argv: list[str]) -> int:
    """Build the archives."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "scripts"))
    import validate

    problems: list[str] = []
    validate.check_proxy_core(problems)
    validate.check_translations(problems, release=args.release)
    if problems:
        print("refusing to build:\n  " + "\n  ".join(problems))
        return 1
    if args.release and validate.main(["--release"]) != 0:
        return 1

    DIST.mkdir(exist_ok=True)
    for stale in DIST.glob("*.zip"):
        stale.unlink()

    current = version()
    managed_zip = DIST / f"proxy_bridge-{current}.zip"
    bootstrap_zip = DIST / f"proxy_bridge_bootstrap-{current}.zip"
    write_zip(managed_zip, MANAGED, ROOT)
    write_zip(
        bootstrap_zip,
        BOOTSTRAP,
        ROOT / "bootstrap",
        extra={"INSTALL.txt": INSTALL_TEXT.format(version=current).encode("utf-8")},
    )

    lines = []
    for archive in (managed_zip, bootstrap_zip):
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        lines.append(f"{digest}  {archive.name}")
        print(
            f"{archive.name:42} {archive.stat().st_size / 1024:7.1f} KiB  {digest[:16]}…"
        )
    sums = "\n".join(lines) + "\n"
    (DIST / "SHA256SUMS").write_text(sums, encoding="utf-8")
    print(f"wrote {DIST.relative_to(ROOT)}/SHA256SUMS")

    (DIST / "RELEASE_NOTES.md").write_text(
        RELEASE_NOTES.format(version=current, sums=sums), encoding="utf-8"
    )
    print(f"wrote {DIST.relative_to(ROOT)}/RELEASE_NOTES.md")

    for readme in README_FILES:
        path = ROOT / readme
        if update_readme(path, lines):
            print(f"updated the checksum table in {readme}")
        else:
            print(f"warning: no checksum markers found in {readme}")
    return 0


def update_readme(path: Path, lines: list[str]) -> bool:
    """Replace the table between the sha256 markers; False when they are absent."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(SHA_BEGIN) + r".*?" + re.escape(SHA_END), re.DOTALL)
    if not pattern.search(text):
        return False
    chinese = "zh" in path.name
    header = "| 安装包 | SHA-256 |" if chinese else "| Archive | SHA-256 |"
    rows = [f"| `{line.split()[1]}` | `{line.split()[0]}` |" for line in lines]
    table = "\n".join([SHA_BEGIN, header, "| --- | --- |", *rows, SHA_END])
    path.write_text(pattern.sub(lambda _: table, text, count=1), encoding="utf-8")
    return True


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Repository self-check for Proxy Bridge.

Runs without Home Assistant installed: it parses files instead of importing the
integration, so contributors can run it straight from a checkout of the repo.
It is also what the CI workflow executes.

    python scripts/validate.py            # development check
    python scripts/validate.py --release  # also reject release blockers
"""

from __future__ import annotations

import argparse
import ast
import json
import py_compile
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANAGED = ROOT / "custom_components" / "proxy_bridge"
BOOTSTRAP = ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap"
PLACEHOLDER = "YOUR_GITHUB_USERNAME"

REQUIRED_MANIFEST_KEYS = (
    "domain",
    "name",
    "codeowners",
    "config_flow",
    "documentation",
    "integration_type",
    "iot_class",
    "issue_tracker",
    "requirements",
    "version",
)


def load_json(path: Path, problems: list[str]) -> Any:
    """Load JSON and record a problem instead of raising."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        problems.append(f"{rel(path)}: invalid JSON ({err})")
        return None


def rel(path: Path) -> str:
    """Return a repository-relative path for messages."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_constant(path: Path, name: str, problems: list[str]) -> str | None:
    """Read a module level string constant without importing the module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as err:
        problems.append(f"{rel(path)}: cannot parse ({err})")
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return str(node.value.value)
    problems.append(f"{rel(path)}: missing {name}")
    return None


def check_proxy_core(problems: list[str]) -> None:
    """The two integrations must ship the exact same transport layer."""
    managed = MANAGED / "proxy_core.py"
    bootstrap = BOOTSTRAP / "proxy_core.py"
    if not managed.exists() or not bootstrap.exists():
        problems.append("proxy_core.py missing in one of the integrations")
        return
    if managed.read_bytes() != bootstrap.read_bytes():
        problems.append(
            "proxy_core.py differs between custom_components/proxy_bridge and "
            "bootstrap/custom_components/proxy_bridge_bootstrap "
            "(run: cp custom_components/proxy_bridge/proxy_core.py "
            "bootstrap/custom_components/proxy_bridge_bootstrap/proxy_core.py)"
        )


def check_manifest(
    component: Path, problems: list[str], *, release: bool
) -> dict[str, Any] | None:
    """Validate one integration manifest."""
    manifest = load_json(component / "manifest.json", problems)
    if not isinstance(manifest, dict):
        return None

    for key in REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            problems.append(f"{rel(component)}/manifest.json: missing {key}")

    if manifest.get("domain") != component.name:
        problems.append(
            f"{rel(component)}/manifest.json: domain {manifest.get('domain')!r} "
            f"does not match directory {component.name!r}"
        )
    if manifest.get("config_flow") is not True:
        problems.append(f"{rel(component)}/manifest.json: config_flow must be true")
    if manifest.get("requirements"):
        problems.append(
            f"{rel(component)}/manifest.json: requirements must stay empty "
            "(the integration is dependency free)"
        )

    version = read_constant(component / "const.py", "VERSION", problems)
    if version and manifest.get("version") != version:
        problems.append(
            f"{rel(component)}: manifest version {manifest.get('version')} != VERSION {version}"
        )

    if release:
        blob = json.dumps(manifest)
        if PLACEHOLDER in blob:
            problems.append(
                f"{rel(component)}/manifest.json still contains {PLACEHOLDER}"
            )
        if version and version.endswith("-dev"):
            problems.append(f"{rel(component)}: refusing to release a -dev version")
    return manifest


def check_translations(problems: list[str], *, release: bool) -> None:
    """Every translation must mirror the English source keys."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import i18n
    except ImportError as err:  # pragma: no cover - only when the repo is broken
        problems.append(f"scripts/i18n.py: cannot import ({err})")
        return

    for component in (MANAGED, BOOTSTRAP):
        source = i18n.flatten(i18n.load_json(component / "strings.json"))
        languages = sorted((component / "translations").glob("*.json"))
        if not languages:
            problems.append(f"{rel(component)}/translations: no translations")
        for path in languages:
            problems.extend(
                i18n._diff(source, i18n.flatten(i18n.load_json(path)), rel(path))
            )
    if release:
        registry = i18n.load_json(ROOT / "i18n" / "languages.json")
        for language, meta in registry.items():
            if language != "en" and meta.get("status") == "needs-translation":
                problems.append(
                    f"i18n/languages.json: {language} is still marked needs-translation"
                )


def check_python(problems: list[str]) -> None:
    """Byte-compile every Python file (catches syntax errors quickly)."""
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in {".venv-proxy", "dist", "__pycache__"} for part in path.parts):
            continue
        try:
            py_compile.compile(str(path), doraise=True, cfile=str(path) + ".pyc")
        except py_compile.PyCompileError as err:
            problems.append(f"{rel(path)}: {err.msg}")
        finally:
            pyc = Path(str(path) + ".pyc")
            if pyc.exists():
                pyc.unlink()


def check_json(problems: list[str]) -> None:
    """All JSON files in the repository must parse."""
    for path in sorted(ROOT.rglob("*.json")):
        if any(part in {".venv-proxy", "node_modules"} for part in path.parts):
            continue
        load_json(path, problems)


def check_hacs(problems: list[str]) -> None:
    """HACS needs hacs.json at the repository root."""
    data = load_json(ROOT / "hacs.json", problems)
    if not isinstance(data, dict):
        return
    if "name" not in data:
        problems.append("hacs.json: missing name")
    if "homeassistant" not in data:
        problems.append("hacs.json: missing the minimum homeassistant version")


def check_strings_usage(problems: list[str]) -> None:
    """Every translation step should be referenced from a flow method."""
    sys.path.insert(0, str(ROOT / "scripts"))

    for component, flow_file in (
        (MANAGED, MANAGED / "config_flow.py"),
        (BOOTSTRAP, BOOTSTRAP / "config_flow.py"),
    ):
        source = load_json(component / "strings.json", problems) or {}
        flow = flow_file.read_text(encoding="utf-8")
        for section in ("config", "options"):
            steps = source.get(section, {}).get("step", {})
            for step in steps:
                if f"async_step_{step}" not in flow:
                    problems.append(
                        f"{rel(flow_file)}: strings.json defines step {step!r} "
                        "but no async_step_ method exists"
                    )


SHA_BEGIN = "<!-- sha256:begin -->"
SHA_END = "<!-- sha256:end -->"


def check_readme_checksums(problems: list[str]) -> None:
    """The README checksum tables must match the archives in dist/."""
    sums = ROOT / "dist" / "SHA256SUMS"
    if not sums.is_file():
        return
    entries = [line.split() for line in sums.read_text(encoding="utf-8").splitlines()]
    entries = [entry for entry in entries if len(entry) == 2]
    for name in ("README.md", "README.zh-Hans.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        if SHA_BEGIN not in text or SHA_END not in text:
            problems.append(f"{name}: missing the sha256 markers")
            continue
        block = text.split(SHA_BEGIN, 1)[1].split(SHA_END, 1)[0]
        for digest, archive in entries:
            if archive not in block or digest not in block:
                problems.append(
                    f"{name}: checksum table is stale for {archive} "
                    "(run scripts/build_dist.py)"
                )


def main(argv: list[str]) -> int:
    """Run every check and report the result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)

    problems: list[str] = []
    check_proxy_core(problems)
    managed = check_manifest(MANAGED, problems, release=args.release)
    bootstrap = check_manifest(BOOTSTRAP, problems, release=args.release)
    if managed and bootstrap and managed.get("version") != bootstrap.get("version"):
        problems.append("the two integrations must share the same version")
    check_hacs(problems)
    check_translations(problems, release=args.release)
    check_strings_usage(problems)
    check_python(problems)
    check_json(problems)
    check_readme_checksums(problems)

    if problems:
        print("validation failed:\n  " + "\n  ".join(problems))
        return 1
    print(f"validation OK (release={args.release})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Structural checks for the bootstrap package.

The bootstrap copy shares the engine but has its own manifest, config flow and
strings.  These tests catch drift between the two packages without needing a
second Home Assistant instance.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGED = ROOT / "custom_components" / "proxy_bridge"
BOOTSTRAP = ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_domain_matches_folder() -> None:
    """The domain must equal the folder name, or HA will not load it."""
    manifest = _read_json(BOOTSTRAP / "manifest.json")
    assert manifest["domain"] == BOOTSTRAP.name
    assert manifest["config_flow"] is True
    assert manifest["version"] == _read_json(MANAGED / "manifest.json")["version"]


def test_manifest_points_to_bootstrap_docs() -> None:
    """Bootstrap users land in docs/BOOTSTRAP.md, not the generic README."""
    manifest = _read_json(BOOTSTRAP / "manifest.json")
    assert "BOOTSTRAP.md" in manifest["documentation"]


def test_domain_differs_from_managed() -> None:
    """Different domains are what makes the chicken-and-egg trick work."""
    managed = _read_json(MANAGED / "manifest.json")
    bootstrap = _read_json(BOOTSTRAP / "manifest.json")
    assert managed["domain"] != bootstrap["domain"]


def test_config_flow_exposes_both_flows() -> None:
    """A config flow and an options flow, with the conventional factory."""
    tree = ast.parse((BOOTSTRAP / "config_flow.py").read_text(encoding="utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "BootstrapConfigFlow" in classes
    assert "BootstrapOptionsFlow" in classes
    source = (BOOTSTRAP / "config_flow.py").read_text(encoding="utf-8")
    assert "async_get_options_flow" in source


def test_strings_are_a_subset_of_the_managed_tree() -> None:
    """Bootstrap English strings must not invent keys the managed copy lacks."""
    managed = _read_json(MANAGED / "strings.json")
    bootstrap = _read_json(BOOTSTRAP / "strings.json")

    def keys(tree: dict, prefix: str = "") -> set[str]:
        found: set[str] = set()
        for key, value in tree.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                found |= keys(value, path)
            else:
                found.add(path)
        return found

    assert keys(bootstrap) <= keys(managed)


def test_translations_match_the_language_registry() -> None:
    """Every registered language exists in both packages."""
    registry = _read_json(ROOT / "i18n" / "languages.json")
    for language in registry:
        assert (MANAGED / "translations" / f"{language}.json").is_file()
        assert (BOOTSTRAP / "translations" / f"{language}.json").is_file()

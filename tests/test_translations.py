"""Translation catalogs must mirror the English source."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    "managed": ROOT / "custom_components" / "proxy_bridge",
    "bootstrap": ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap",
}
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def flatten(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested translation dictionaries."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, full))
        elif isinstance(value, str):
            flat[full] = value
    return flat


@pytest.mark.parametrize("component", COMPONENTS.values(), ids=COMPONENTS.keys())
def test_english_translation_is_the_source(component: Path) -> None:
    """translations/en.json is generated from strings.json; keep them equal."""
    source = json.loads((component / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (component / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert english == source


@pytest.mark.parametrize("component", COMPONENTS.values(), ids=COMPONENTS.keys())
def test_chinese_catalog_is_complete(component: Path) -> None:
    """zh-Hans must translate every key with the same placeholders."""
    source = flatten(
        json.loads((component / "strings.json").read_text(encoding="utf-8"))
    )
    chinese = flatten(
        json.loads(
            (component / "translations" / "zh-Hans.json").read_text(encoding="utf-8")
        )
    )
    assert set(chinese) == set(source)
    for key, text in chinese.items():
        assert text.strip(), key
        assert set(PLACEHOLDER.findall(text)) == set(
            PLACEHOLDER.findall(source[key])
        ), key


def test_language_registry_lists_shipped_catalogs() -> None:
    """A language without a registry entry cannot be maintained."""
    registry = json.loads(
        (ROOT / "i18n" / "languages.json").read_text(encoding="utf-8")
    )
    shipped = {
        path.stem for path in (COMPONENTS["managed"] / "translations").glob("*.json")
    }
    assert shipped <= set(registry)
    assert registry["en"]["status"] == "source"

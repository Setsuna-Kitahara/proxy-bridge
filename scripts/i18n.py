#!/usr/bin/env python3
"""Translation helper for Proxy Bridge.

Home Assistant loads ``translations/<language>.json`` for every language and
falls back to ``strings.json`` (published as ``translations/en.json``) for
missing keys.  This script keeps the copies honest:

    python scripts/i18n.py build              # strings.json -> translations/en.json
    python scripts/i18n.py check              # key/placeholder parity for every language
    python scripts/i18n.py scaffold fr        # start a new language from English
    python scripts/i18n.py status             # completeness table

Both the managed integration and the bootstrap copy are handled; they share
the same source strings except for a handful of bootstrap-specific ones.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = (
    ROOT / "custom_components" / "proxy_bridge",
    ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap",
)
LANGUAGES_FILE = ROOT / "i18n" / "languages.json"
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

SOURCE_LANGUAGE = "en"


def load_json(path: Path) -> Any:
    """Load a JSON file with a helpful error message."""
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise SystemExit(f"missing file: {path}") from None
    except json.JSONDecodeError as err:
        raise SystemExit(f"invalid JSON in {path}: {err}") from None


def write_json(path: Path, data: Any) -> None:
    """Write pretty JSON with a trailing newline and real unicode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def flatten(data: Any, prefix: str = "") -> dict[str, str]:
    """Flatten nested translation dictionaries into ``a.b.c`` keys."""
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, str):
        flat[prefix] = data
    return flat


def placeholders(text: str) -> set[str]:
    """Return the placeholder names used in ``text``."""
    return set(PLACEHOLDER.findall(text))


def build() -> int:
    """Regenerate ``translations/en.json`` from ``strings.json``."""
    for component in COMPONENTS:
        source = load_json(component / "strings.json")
        write_json(component / "translations" / f"{SOURCE_LANGUAGE}.json", source)
        print(f"built {component.name}/translations/{SOURCE_LANGUAGE}.json")
    return 0


def check() -> int:
    """Verify that every language mirrors the English source exactly."""
    problems: list[str] = []
    registry = load_json(LANGUAGES_FILE) if LANGUAGES_FILE.exists() else {}

    for component in COMPONENTS:
        name = component.name
        source = flatten(load_json(component / "strings.json"))
        translations = component / "translations"

        english = flatten(load_json(translations / f"{SOURCE_LANGUAGE}.json"))
        if english != source:
            problems.append(f"{name}: translations/{SOURCE_LANGUAGE}.json is stale")
            problems.extend(_diff(source, english, f"{name}/{SOURCE_LANGUAGE}"))

        for path in sorted(translations.glob("*.json")):
            language = path.stem
            if language == SOURCE_LANGUAGE:
                continue
            data = flatten(load_json(path))
            problems.extend(_diff(source, data, f"{name}/{language}"))
            if language not in registry:
                problems.append(f"{name}/{language}: not listed in i18n/languages.json")

    if SOURCE_LANGUAGE not in registry:
        problems.append("i18n/languages.json: missing the source language")

    if problems:
        print("translation problems:\n  " + "\n  ".join(problems))
        return 1
    print("translations OK")
    return 0


def _diff(source: dict[str, str], target: dict[str, str], label: str) -> list[str]:
    """Compare keys, placeholders and emptiness between two catalogs."""
    problems: list[str] = []
    missing = sorted(set(source) - set(target))
    extra = sorted(set(target) - set(source))
    problems.extend(f"{label}: missing key {key}" for key in missing)
    problems.extend(f"{label}: unknown key {key}" for key in extra)
    for key in sorted(set(source) & set(target)):
        if not target[key].strip():
            problems.append(f"{label}: empty value for {key}")
        expected = placeholders(source[key])
        actual = placeholders(target[key])
        if expected != actual:
            problems.append(
                f"{label}: placeholders for {key} are {sorted(actual)}, expected {sorted(expected)}"
            )
    return problems


def scaffold(language: str) -> int:
    """Create an untranslated catalog for ``language`` plus a registry entry."""
    if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*", language):
        raise SystemExit(
            f"{language!r} does not look like a Home Assistant language tag"
        )
    if language == SOURCE_LANGUAGE:
        raise SystemExit("English is the source language")

    for component in COMPONENTS:
        target = component / "translations" / f"{language}.json"
        if target.exists():
            print(f"keeps existing {target.relative_to(ROOT)}")
            continue
        write_json(target, load_json(component / "strings.json"))
        print(f"created {target.relative_to(ROOT)} (still English, translate it)")

    registry = load_json(LANGUAGES_FILE) if LANGUAGES_FILE.exists() else {}
    registry.setdefault(
        language,
        {
            "name": language,
            "native_name": language,
            "status": "needs-translation",
            "maintainers": [],
        },
    )
    write_json(LANGUAGES_FILE, registry)
    print("add yourself to maintainers in i18n/languages.json")
    return 0


def status() -> int:
    """Print a small completeness table per language."""
    registry = load_json(LANGUAGES_FILE) if LANGUAGES_FILE.exists() else {}
    rows: list[tuple[str, int, int]] = []
    source = flatten(load_json(COMPONENTS[0] / "strings.json"))
    for path in sorted((COMPONENTS[0] / "translations").glob("*.json")):
        data = flatten(load_json(path))
        same = sum(1 for key, value in source.items() if data.get(key) == value)
        if path.stem == SOURCE_LANGUAGE:
            same = 0
        rows.append((path.stem, len(source), same))

    print(f"{'language':12} {'status':18} translated")
    for language, total, same in rows:
        status_value = registry.get(language, {}).get("status", "unknown")
        translated = total if language == SOURCE_LANGUAGE else total - same
        print(f"{language:12} {status_value:18} {translated}/{total}")
    return 0


def main(argv: list[str]) -> int:
    """Run the requested command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "check", "scaffold", "status"))
    parser.add_argument("language", nargs="?")
    args = parser.parse_args(argv)

    if args.command == "build":
        return build()
    if args.command == "check":
        return check()
    if args.command == "status":
        return status()
    if not args.language:
        parser.error("scaffold needs a language tag, for example: scaffold fr")
    return scaffold(args.language)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

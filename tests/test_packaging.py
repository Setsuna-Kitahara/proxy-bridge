"""Packaging invariants: the two integrations must stay in lock step."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANAGED = ROOT / "custom_components" / "proxy_bridge"
BOOTSTRAP = ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_proxy_core_is_shared_verbatim() -> None:
    """Both integrations ship the exact same transport layer."""
    assert (MANAGED / "proxy_core.py").read_bytes() == (
        BOOTSTRAP / "proxy_core.py"
    ).read_bytes()


@pytest.mark.parametrize(
    "component", [MANAGED, BOOTSTRAP], ids=["managed", "bootstrap"]
)
def test_manifest_matches_directory(component: Path) -> None:
    """The manifest domain must match the folder HACS copies into /config."""
    manifest = json.loads(_read(component / "manifest.json"))
    assert manifest["domain"] == component.name
    assert manifest["config_flow"] is True
    assert manifest["version"] == "1.0.1"


def test_both_components_share_a_version() -> None:
    """HACS installs the managed copy; the bootstrap must not drift."""
    managed = json.loads(_read(MANAGED / "manifest.json"))
    bootstrap = json.loads(_read(BOOTSTRAP / "manifest.json"))
    assert managed["version"] == bootstrap["version"]


def test_repository_validation_passes() -> None:
    """scripts/validate.py is the single source of truth for repo checks."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_readme_checksum_table_is_regenerated(tmp_path: Path) -> None:
    """build_dist rewrites the table between the markers, in both languages."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_dist

    readme = tmp_path / "README.zh-Hans.md"
    readme.write_text(
        f"intro\n{build_dist.SHA_BEGIN}\n| old | old |\n{build_dist.SHA_END}\noutro\n",
        encoding="utf-8",
    )
    lines = [
        f"{'a' * 64}  proxy_bridge-1.0.1.zip",
        f"{'b' * 64}  proxy_bridge_bootstrap-1.0.1.zip",
    ]
    assert build_dist.update_readme(readme, lines) is True
    text = readme.read_text(encoding="utf-8")
    assert "| 安装包 | SHA-256 |" in text
    assert f"`{'a' * 64}`" in text
    assert "| old | old |" not in text
    assert text.startswith("intro") and text.endswith("outro\n")


def test_validate_reports_a_stale_checksum_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the zips change but the README is not rebuilt, validation must fail."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import validate

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "SHA256SUMS").write_text(
        f"{'c' * 64}  proxy_bridge-1.0.1.zip\n", encoding="utf-8"
    )
    for name in ("README.md", "README.zh-Hans.md"):
        (tmp_path / name).write_text(
            f"{validate.SHA_BEGIN}\n| x | y |\n{validate.SHA_END}\n", encoding="utf-8"
        )
    monkeypatch.setattr(validate, "ROOT", tmp_path)
    problems: list[str] = []
    validate.check_readme_checksums(problems)
    assert len(problems) == 2
    assert "stale" in problems[0]

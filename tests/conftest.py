"""Pytest bootstrap: make the integration folder importable without HA."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "proxy_bridge"
TESTS = Path(__file__).resolve().parent

for entry in (str(ROOT), str(PACKAGE), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# The bootstrap integration ships an identical copy of proxy_core.py; keep the
# byte-for-byte requirement honest with a single test (see test_packaging.py).
BOOTSTRAP_PACKAGE = ROOT / "bootstrap" / "custom_components" / "proxy_bridge_bootstrap"


@pytest.fixture(autouse=True)
def _allow_loopback_sockets() -> Iterator[None]:
    """Re-enable sockets that the HA test plugin blocks globally.

    ``pytest-homeassistant-custom-component`` disables ``socket.socket`` for
    every test (even tests that never touch Home Assistant).  The bridge tests
    need a real loopback listener, so undo that when the plugin is installed.
    Runs as a normal autouse fixture, i.e. after the plugin's setup hook.
    """
    try:
        import pytest_socket
    except ImportError:
        yield
        return
    pytest_socket.enable_socket()
    yield


@pytest.fixture
def verify_cleanup() -> Iterator[None]:
    """Shadow the HA plugin's leak check.

    That check fails on the background threads aiohttp starts for proxy
    authentication, which are unrelated to Home Assistant correctness.  The
    config-flow tests still fail on real errors via ``hass`` itself.
    """
    yield

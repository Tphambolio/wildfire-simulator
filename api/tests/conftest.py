"""Shared API test setup.

The "pyra" fire-weather tier (``services/pyra.py``) runs before the CWFIS tiers and makes its own
HTTP calls. Tests that pin the CWFIS / GEM tiers run with it switched off; tests of the Pyra tier
are marked ``@pytest.mark.pyra`` and get it switched on with an empty cache.
"""

from __future__ import annotations

import pytest

from firesim_api.services import pyra as pyra_source


def pytest_configure(config):
    config.addinivalue_line("markers", "pyra: test runs with the Pyra fire-weather tier enabled")


@pytest.fixture(autouse=True)
def _pyra_tier(request, monkeypatch):
    enabled = request.node.get_closest_marker("pyra") is not None
    monkeypatch.setenv("FIRESIM_PYRA_SOURCE", "1" if enabled else "0")
    pyra_source.reset_cache()
    yield
    pyra_source.reset_cache()

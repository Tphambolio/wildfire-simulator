"""``scripts/validate.py run --start-types``: keep only fire-days of the given start types.

The start type (point / fresh / mature; firesim.validation.start_strata, [R58]) is looked up per
fire-day; the filter is applied before the runs, so other fire-days are never run. Mechanics
only: the lookup and the worker pool are stubbed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def V(monkeypatch):
    spec = importlib.util.spec_from_file_location("validate_cli_start_types", REPO / "scripts/validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakePool:
    seen: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def imap_unordered(self, fn, tasks):
        _FakePool.seen = list(tasks)
        return iter(())


def _run(V, monkeypatch, tmp_path, extra):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"fire_id": "A", "days": [1, 2]}, {"fire_id": "B", "days": [3]}]))
    types = {("A", 1): "point", ("A", 2): "mature", ("B", 3): "fresh"}
    monkeypatch.setattr(V, "manifest_path", lambda: manifest)
    monkeypatch.setattr(V, "_start_type_of", lambda fid, day: {"start_type": types[(fid, day)]})
    monkeypatch.setattr(V, "Pool", _FakePool)
    monkeypatch.setattr(sys, "argv", ["validate.py", "--runs-dir", str(tmp_path / "runs"), "run",
                                      "--name", "t", *extra])
    V.main()
    return sorted((t["fire_id"], t["day"]) for t in _FakePool.seen)


def test_start_types_filter(V, monkeypatch, tmp_path):
    assert _run(V, monkeypatch, tmp_path, ["--start-types", "mature"]) == [("A", 2)]
    assert _run(V, monkeypatch, tmp_path, ["--start-types", "point", "fresh"]) == [("A", 1), ("B", 3)]


def test_no_flag_runs_all(V, monkeypatch, tmp_path):
    assert _run(V, monkeypatch, tmp_path, []) == [("A", 1), ("A", 2), ("B", 3)]

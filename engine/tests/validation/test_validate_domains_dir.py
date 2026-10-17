"""``scripts/validate.py --domains-dir``: score the same fire-days on another fuel grid.

The run stages read fire domains from ``$FIRESIM_VALIDATION_DOMAINS`` (set by ``--domains-dir``)
instead of ``$FIRESIM_VALIDATION_DATA/domains``, and each run record names the domain set and
its fuel source. Uses the committed CFSDS fixture (synthetic fuel), so it checks mechanics only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from firesim.validation.cfsds import FireDomain
from firesim.validation.weather import HourRecord

REPO = Path(__file__).resolve().parents[3]
DATA = Path(__file__).parent / "data"


@pytest.fixture
def V(monkeypatch):
    """A fresh import of scripts/validate.py with no domain override in the environment."""
    monkeypatch.delenv("FIRESIM_VALIDATION_DOMAINS", raising=False)
    spec = importlib.util.spec_from_file_location("validate_cli_test", REPO / "scripts/validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _domain_sets(tmp_path: Path) -> tuple[Path, Path, FireDomain]:
    """Two domain directories for the fixture fire: the original fuel and an all-non-fuel copy."""
    dom = FireDomain.load(DATA / "cfsds_fireday_fixture.npz")
    a, b = tmp_path / "domains_a", tmp_path / "domains_b"
    a.mkdir()
    b.mkdir()
    dom.save(a / f"{dom.fire_id}.npz")
    other = replace(dom, fuel=np.full(dom.fuel.shape, 101, np.int32), fuel_scheme="cfs_national",
                    meta=dict(dom.meta, fuel_source="test grid b"))
    other.save(b / f"{dom.fire_id}.npz")
    return a, b, dom


def test_default_is_validation_data_domains(V):
    assert V.domains_dir() == V.ROOT / "domains"


def test_environment_override(V, monkeypatch, tmp_path):
    monkeypatch.setenv("FIRESIM_VALIDATION_DOMAINS", str(tmp_path))
    assert V.domains_dir() == tmp_path
    monkeypatch.setenv("FIRESIM_VALIDATION_DOMAINS", "  ")
    assert V.domains_dir() == V.ROOT / "domains"


def test_load_domain_reads_the_chosen_set(V, monkeypatch, tmp_path):
    a, b, dom = _domain_sets(tmp_path)
    monkeypatch.setenv("FIRESIM_VALIDATION_DOMAINS", str(a))
    da = V.load_domain(dom.fire_id)
    monkeypatch.setenv("FIRESIM_VALIDATION_DOMAINS", str(b))
    db = V.load_domain(dom.fire_id)
    np.testing.assert_array_equal(da.fuel, dom.fuel)
    assert (db.fuel == 101).all() and db.fuel_scheme == "cfs_national"
    np.testing.assert_array_equal(da.dob, db.dob)  # only the fuel differs
    with pytest.raises(FileNotFoundError, match="domains_b"):
        V.load_domain("1999_1")


def test_cli_sets_the_override_for_workers(V, monkeypatch, tmp_path):
    a, _, _ = _domain_sets(tmp_path)
    monkeypatch.setenv("FIRESIM_VALIDATION_DOMAINS", "")  # main() writes it; restored afterwards
    monkeypatch.setattr(sys, "argv", ["validate.py", "--domains-dir", str(a), "--runs-dir",
                                      str(tmp_path / "runs"), "report"])
    (tmp_path / "runs").mkdir()
    V.main()
    assert os.environ["FIRESIM_VALIDATION_DOMAINS"] == str(a.resolve())
    assert V.domains_dir() == a.resolve()
    assert (tmp_path / "report.md").exists()


def test_cli_rejects_domains_dir_for_prepare(V, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["validate.py", "--domains-dir", str(tmp_path), "prepare"])
    with pytest.raises(SystemExit):
        V.main()


def test_run_record_names_domain_set_and_fuel(V, monkeypatch, tmp_path):
    """A run task on each domain set: records carry the set and its fuel; scores differ."""
    a, b, dom = _domain_sets(tmp_path)
    meta = json.loads((DATA / "cfsds_fireday_fixture.json").read_text())
    groups = {int(k): v for k, v in meta["groups"].items()}
    records = [HourRecord(datetime.fromisoformat(t), *vals) for t, *vals in meta["hours"]]
    monkeypatch.setattr(V, "_load_fire", lambda fid: (V.load_domain(fid), groups, records))
    task = {"fire_id": dom.fire_id, "day": meta["day"], "ignition": "perimeter",
            "spotting": False, "start_hour": 6, "windows": [4.0], "margin_m": 3000.0,
            "wind_members": False, "active_days": 1, "active_buffer_m": None,
            "ffmc_spinup": False, "burning_period": None, "ensemble": None}
    out = {}
    for name, d in (("a", a), ("b", b)):
        monkeypatch.setenv("FIRESIM_VALIDATION_DOMAINS", str(d))
        out[name] = V._run_task(dict(task))
        assert "error" not in out[name], out[name]
        assert out[name]["domains_dir"] == str(d)
    assert out["a"]["fuel_source"] == dom.meta.get("fuel_source", "")
    assert out["b"]["fuel_source"] == "test grid b"
    # all non-fuel: nothing can spread, so the predicted growth is empty
    assert out["b"]["members"]["det"]["4h"]["area_pred"] == 0.0
    assert out["a"]["members"]["det"]["4h"]["area_pred"] > 0.0

"""Start-type strata and start-perimeter diagnostics (backlog A2; descriptive only).

Synthetic grids check the tagging rule, the 500 m boundary sampling, the static share and the
growth ratio; the committed CFSDS fixture checks that the harness records them without
changing any existing score.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from firesim.validation.cfsds import FireDomain
from firesim.validation.harness import RunOptions, build_case, run_fire_day
from firesim.validation.metrics import overlap_scores
from firesim.validation.report import flatten, start_strata_summary, summarize
from firesim.validation.start_strata import (
    POINT_MAX_HA,
    StartPerimeterDiagnostic,
    boundary_samples,
    growth_ratio,
    start_type,
)
from firesim.validation.weather import HourRecord

DATA = Path(__file__).parent / "data"
CELL = 100.0  # m; cell area 1 ha


def _dob(shape=(60, 60)) -> np.ndarray:
    return np.zeros(shape, dtype=np.int32)


class TestStartType:
    def test_point_start_small_and_one_day_old(self):
        dob = _dob()
        dob[10:15, 10:15] = 199  # 25 ha, all burned the day before
        dob[15:30, 10:30] = 200
        t = start_type(dob, 200, CELL * CELL)
        assert t["start_type"] == "point"
        assert t["start_age_days"] == 1 and t["start_ha"] == pytest.approx(25.0)
        assert t["start_prev_day_share"] == pytest.approx(1.0)

    def test_point_threshold_is_inclusive(self):
        dob = _dob()
        dob[0:10, 0:10] = 199  # exactly 100 ha
        assert start_type(dob, 200, CELL * CELL)["start_type"] == "point"
        dob[10, 0] = 199  # 101 ha
        assert start_type(dob, 200, CELL * CELL)["start_type"] == "fresh"
        assert POINT_MAX_HA == 100.0

    def test_fresh_perimeter_large_and_one_day_old(self):
        dob = _dob()
        dob[0:20, 0:20] = 199  # 400 ha
        assert start_type(dob, 200, CELL * CELL)["start_type"] == "fresh"

    def test_mature_any_area_two_or_more_days_old(self):
        dob = _dob()
        dob[0:2, 0:2] = 197  # tiny but old
        dob[2:4, 0:2] = 199
        t = start_type(dob, 200, CELL * CELL)
        assert t["start_type"] == "mature" and t["start_age_days"] == 3
        assert t["start_prev_day_share"] == pytest.approx(0.5)

    def test_uses_no_burn_day_information(self):
        dob = _dob()
        dob[0:20, 0:20] = 199
        a = start_type(dob, 200, CELL * CELL)
        dob[20:60, :] = 200  # burn-day growth and later days do not change the tag
        dob[0:5, 30:40] = 205
        assert start_type(dob, 200, CELL * CELL) == a

    def test_no_start_area_is_point(self):
        t = start_type(_dob(), 200, CELL * CELL)
        assert t["start_type"] == "point" and t["start_ha"] == 0.0


class TestBoundarySamples:
    def test_spacing_about_500_m(self):
        # 6 x 6 km square of 100 m cells -> perimeter 24 km -> about 48 samples at 500 m
        start = np.zeros((100, 100), dtype=bool)
        start[20:80, 20:80] = True
        rr, cc = boundary_samples(start, CELL, CELL, 500.0)
        assert 40 <= len(rr) <= 56
        assert start[rr, cc].all()
        # every sample is a boundary cell (has a 4-neighbour outside)
        assert all(not (start[r - 1, c] and start[r + 1, c] and start[r, c - 1] and start[r, c + 1])
                   for r, c in zip(rr, cc))

    def test_deterministic(self):
        rng = np.random.default_rng(3)
        start = rng.random((80, 80)) > 0.4
        a = boundary_samples(start, 90.0, 92.0)
        b = boundary_samples(start, 90.0, 92.0)
        assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])

    def test_empty(self):
        rr, _ = boundary_samples(np.zeros((5, 5), dtype=bool), CELL, CELL)
        assert len(rr) == 0


class TestStaticShare:
    @pytest.fixture
    def square(self):
        start = np.zeros((120, 120), dtype=bool)
        start[40:80, 40:80] = True  # 4 x 4 km
        return start

    def test_one_side_moved(self, square):
        growth = np.zeros_like(square)
        growth[40:80, 80:100] = True  # east side advanced 2 km
        diag = StartPerimeterDiagnostic(square, growth, CELL, CELL)
        s = diag.summary(growth)
        # about 3 of 4 sides static (corner samples may go either way)
        assert 0.65 <= s["static_share"] <= 0.82
        assert s["share_adv_gt_3km"] == 0.0

    def test_all_sides_moved_and_nothing_moved(self, square):
        grown = np.zeros_like(square)
        grown[25:95, 25:95] = True
        grown &= ~square  # 1.5 km all round
        diag = StartPerimeterDiagnostic(square, grown, CELL, CELL)
        assert diag.summary(grown)["static_share"] == 0.0
        assert diag.summary(np.zeros_like(square))["static_share"] == 1.0
        assert diag.summary(grown)["adv_median_km"] == pytest.approx(1.5, abs=0.15)

    def test_small_advance_counts_as_static(self, square):
        grown = np.zeros_like(square)
        grown[35:85, 35:85] = True  # 0.5 km all round
        diag = StartPerimeterDiagnostic(square, grown, CELL, CELL)
        assert diag.summary(grown)["static_share"] == 1.0

    def test_growth_outside_reach_is_ignored(self, square):
        reach = np.zeros_like(square)
        growth = np.zeros_like(square)
        growth[40:80, 80:100] = True
        diag = StartPerimeterDiagnostic(square, reach, CELL, CELL)
        assert diag.summary(growth)["static_share"] == 1.0

    def test_no_start_gives_nan(self):
        z = np.zeros((10, 10), dtype=bool)
        s = StartPerimeterDiagnostic(z, ~z, CELL, CELL).summary(~z)
        assert math.isnan(s["static_share"])


def test_growth_ratio():
    assert growth_ratio(300.0, 100.0) == pytest.approx(3.0)
    assert growth_ratio(0.0, 100.0) == 0.0
    assert math.isnan(growth_ratio(10.0, 0.0))


# ---------------------------------------------------------------------------------------------
# On the committed CFSDS fixture (fire 2016_106, burn day 199; synthetic fuel)
# ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixture_record():
    dom = FireDomain.load(DATA / "cfsds_fireday_fixture.npz")
    meta = json.loads((DATA / "cfsds_fireday_fixture.json").read_text())
    groups = {int(k): v for k, v in meta["groups"].items()}
    records = [HourRecord(datetime.fromisoformat(t), *vals) for t, *vals in meta["hours"]]
    case = build_case(dom, meta["day"], groups, records, start_hour=6, hours=24, margin_m=3000.0)
    opts = RunOptions(windows_h=(4.0, 8.0), oracle_max_h=8)
    from firesim.validation.harness import simulate

    arrival = simulate(case, opts)["arrival"]
    return case, opts, run_fire_day(case, opts), arrival


class TestHarnessRecord:
    def test_start_type_recorded(self, fixture_record):
        case, _, rec, _ = fixture_record
        # day 199: start area burned on days 196-198 -> mature
        assert rec["start_type"] == "mature" and rec["start_age_days"] == 3
        # 47 + 76 + 21 cells burned before day 199 (test_harness); the model's start area also
        # fills holes inside the outline, so initial_ha >= start_ha
        assert rec["start_ha"] == pytest.approx(144 * case.domain.cell_area_m2 / 1e4)
        assert rec["start_ha"] <= rec["initial_ha"]

    def test_diagnostics_recorded(self, fixture_record):
        _, _, rec, _ = fixture_record
        obs = rec["start_perimeter_obs"]
        assert obs["n_samples"] > 0 and 0.0 <= obs["static_share"] <= 1.0
        for w in ("4h", "8h"):
            m = rec["members"]["det"][w]
            assert 0.0 <= m["static_share_model"] <= 1.0
            assert m["growth_ratio"] == pytest.approx(m["area_pred"] / m["area_obs"])
        assert "start_perimeter_obs" not in rec["members"]["det"]

    def test_existing_scores_unchanged(self, fixture_record):
        """The scores recompute exactly from the arrival grid as before A2."""
        case, opts, rec, arrival = fixture_record
        dom = case.domain
        prior = (dom.dob > 0) & (dom.dob < case.day)
        excluded = (arrival <= 0.0) | prior
        obs = (dom.dob == case.day) & ~excluded
        for w in opts.windows_h:
            s = overlap_scores((arrival <= w * 60.0) & ~excluded, obs, dom.cell_area_m2)
            m = rec["members"]["det"][f"{w:g}h"]
            for k, v in s.as_dict().items():
                assert (math.isnan(v) and math.isnan(m[k])) or m[k] == v

    def test_flatten_and_strata_summary(self, fixture_record):
        _, _, rec, _ = fixture_record
        rows = [r for r in flatten([rec]) if r["window"] == "8h"]
        assert rows[0]["start_type"] == "mature"
        ss = start_strata_summary(rows)
        assert list(ss) == ["mature", "all"]
        assert ss["all"]["f1_mean"] == pytest.approx(summarize(rows)["f1"]["mean"])
        assert ss["mature"]["n_fire_days"] == 1


def test_strata_summary_pooled_equals_union():
    rows = [{"fire_id": f"f{i % 3}", "f1": f, "precision": 0.5, "recall": 0.5, "iou": 0.2,
             "area_diff_norm": 0.1, "start_type": t, "static_share_obs": so,
             "static_share_model": sm, "growth_ratio": g}
            for i, (f, t, so, sm, g) in enumerate([
                (0.2, "point", math.nan, 0.0, 2.0), (0.4, "mature", 0.6, 0.1, 4.0),
                (float("nan"), "mature", 0.8, 0.0, 0.0), (0.1, "fresh", 0.1, 0.0, 1.0)])]
    ss = start_strata_summary(rows)
    assert list(ss) == ["point", "fresh", "mature", "all"]
    assert ss["all"]["n_fire_days"] == 4 and ss["all"]["f1_mean"] == pytest.approx(0.175)
    assert ss["mature"]["f1_mean"] == pytest.approx(0.2)  # NaN F1 counts as 0
    assert ss["mature"]["static_share_obs_median"] == pytest.approx(0.7)
    assert ss["all"]["growth_ratio_median"] == pytest.approx(1.5)
    assert ss["all"]["growth_ratio_geomean"] == pytest.approx((2.0 * 4.0 * 1.0) ** (1 / 3))
    assert ss["all"]["n_growth_ratio_zero"] == 1
    assert ss["all"]["growth_ratio_share_gt1"] == pytest.approx(0.5)


def test_validate_report_and_compare_strata(fixture_record, monkeypatch, tmp_path):
    """``validate.py report`` writes per-stratum tables and start_strata.json; ``compare``
    appends a per-stratum table; records written before A2 are tagged from the domain."""
    import importlib.util
    import sys

    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("validate_strata_test",
                                                  repo / "scripts/validate.py")
    V = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(V)
    case, _, rec, _ = fixture_record
    doms = tmp_path / "domains"
    doms.mkdir()
    FireDomain.load(DATA / "cfsds_fireday_fixture.npz").save(doms / f"{case.fire_id}.npz")
    runs = tmp_path / "runs"
    runs.mkdir()
    new = json.loads(json.dumps(rec, default=V._json_default))
    old = {k: v for k, v in new.items() if not k.startswith("start_")}  # a pre-A2 record
    old["members"] = {"det": {w: ({k: v for k, v in s.items() if k not in (
        "growth_ratio", "static_share_model", "share_adv_gt_3km_model", "adv_median_km_model")}
        if isinstance(s, dict) else s) for w, s in new["members"]["det"].items()}}
    (runs / "new.jsonl").write_text(json.dumps(new) + "\n")
    (runs / "old.jsonl").write_text(json.dumps(old) + "\n")
    monkeypatch.setattr(sys, "argv", ["validate.py", "--domains-dir", str(doms), "--runs-dir",
                                      str(runs), "report"])
    V.main()
    text = (tmp_path / "report.md").read_text()
    assert "By start type" in text and "| mature | 1 | 1 |" in text
    js = json.loads((tmp_path / "start_strata.json").read_text())
    assert "_about" in js
    assert js["runs"]["old"]["8h"]["mature"]["n_fire_days"] == 1  # tagged from the domain
    assert js["runs"]["new"]["8h"]["all"]["f1_mean"] == js["runs"]["old"]["8h"]["all"]["f1_mean"]
    assert math.isnan(js["runs"]["old"]["8h"]["all"]["growth_ratio_median"])
    out = tmp_path / "cmp.md"
    monkeypatch.setattr(sys, "argv", ["validate.py", "--domains-dir", str(doms), "--runs-dir",
                                      str(runs), "compare", "old", "new", "--ref", "old",
                                      "--windows", "8h", "--out", str(out)])
    V.main()
    cmp = out.read_text()
    assert "By start type" in cmp and "| new | 8h | " in cmp and "| mature | 1 | 1 |" in cmp

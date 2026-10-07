"""End-to-end fire-day validation on a tiny committed CFSDS fixture, plus harness pieces.

The fixture (data/make_fixture.py) is a real CFSDS day-of-burning crop (fire 2016_106, July
2016) with real ERA5 hourly weather, but synthetic fuel (uniform C-2 with a lake) and terrain.
These tests check the harness mechanics, not FireSim's accuracy.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.types import HourlyWeather
from firesim.validation.cfsds import FireDomain, fire_day_pairs, read_groups
from firesim.validation.harness import (
    FireDayCase,
    Member,
    RunOptions,
    build_case,
    crop_for_day,
    fuel_grid_for,
    initial_state,
    run_fire_day,
    terrain_grid_for,
    wind_direction_members,
)
from firesim.validation.report import flatten, growth_class, per_fire_means, summarize
from firesim.validation.weather import HourRecord, burn_hours, parse_open_meteo

DATA = Path(__file__).parent / "data"
FAST = RunOptions(windows_h=(4.0, 8.0), oracle_max_h=8)


@pytest.fixture(scope="module")
def fixture():
    dom = FireDomain.load(DATA / "cfsds_fireday_fixture.npz")
    meta = json.loads((DATA / "cfsds_fireday_fixture.json").read_text())
    groups = {int(k): v for k, v in meta["groups"].items()}
    records = [HourRecord(datetime.fromisoformat(t), *vals) for t, *vals in meta["hours"]]
    return dom, groups, records, meta["day"]


@pytest.fixture(scope="module")
def case(fixture):
    dom, groups, records, day = fixture
    return build_case(dom, day, groups, records, start_hour=6, hours=24, margin_m=3000.0)


class TestFixture:
    def test_domain_contents(self, fixture):
        dom, _, _, day = fixture
        assert dom.fire_id == "2016_106" and dom.year == 2016
        assert dom.burn_days() == [196, 197, 198, 199, 200]
        assert int((dom.dob == day).sum()) == 428
        assert dom.dx == pytest.approx(90.0, rel=1e-3) and dom.dy == pytest.approx(90.0, rel=1e-3)

    def test_fire_day_pairs(self, fixture):
        dom = fixture[0]
        pairs = fire_day_pairs(dom)
        assert [p.day for p in pairs] == [197, 198, 199, 200]
        p = next(p for p in pairs if p.day == 199)
        assert p.growth_cells == 428 and p.previous_day_cells == 21
        assert p.initial_cells == 47 + 76 + 21 and p.adjacent

    def test_case_uses_previous_day_codes(self, case, fixture):
        groups = fixture[1]
        assert case.ffmc == pytest.approx(groups[198]["ffmc"])
        assert case.dc == pytest.approx(groups[198]["dc"])
        assert case.fwi == pytest.approx(groups[199]["fwi"])
        assert case.start == datetime(2016, 7, 17, 6)
        assert len(case.hourly) == 24 and case.hourly[0].hours_from_start == 0.0


class TestEndToEnd:
    def test_perimeter_run(self, case):
        rec = run_fire_day(case, FAST)
        m = rec["members"]["det"]
        assert set(m) >= {"4h", "8h", "oracle", "edge_hit", "run_s"}
        # observed growth is the 428 day-199 cells
        assert rec["obs_growth_ha"] == pytest.approx(428 * case.domain.cell_area_m2 / 1e4)
        for w in ("4h", "8h"):
            s = m[w]
            for k in ("precision", "recall", "f1", "iou"):
                assert 0.0 <= s[k] <= 1.0
            assert -1.0 <= s["area_diff_norm"] <= 1.0
            assert s["hausdorff_m"] > 0
        # Longer burning can only add area
        assert m["8h"]["area_pred"] >= m["4h"]["area_pred"]
        assert 1 <= m["oracle"]["hour"] <= 8
        assert m["oracle"]["f1"] >= max(m["4h"]["f1"], m["8h"]["f1"]) - 1e-12
        assert rec["dominant_fuel"] == "C2" and rec["fwi_class"] is not None
        json.dumps(rec)  # JSON-friendly

    def test_deterministic(self, case):
        a = run_fire_day(case, FAST)["members"]["det"]["8h"]
        b = run_fire_day(case, FAST)["members"]["det"]["8h"]
        assert a["tp"] == b["tp"] and a["fp"] == b["fp"]

    def test_spotting_runs_repeatable(self, case):
        opts = RunOptions(windows_h=(8.0,), oracle_max_h=8, enable_spotting=True)
        a = run_fire_day(case, opts)["members"]["det"]["8h"]
        b = run_fire_day(case, opts)["members"]["det"]["8h"]
        assert a["tp"] == b["tp"] and a["fp"] == b["fp"]

    def test_bennett_ignition(self, case):
        rec = run_fire_day(case, RunOptions(windows_h=(8.0,), oracle_max_h=8, ignition="bennett"))
        assert rec["ignition"] == "bennett"
        assert 0.0 <= rec["members"]["det"]["8h"]["f1"] <= 1.0

    def test_wind_members(self, case):
        members = (Member("a", constant_wind_direction=0.0), Member("b", constant_wind_direction=180.0))
        rec = run_fire_day(case, RunOptions(windows_h=(4.0,), oracle_max_h=4), members)
        assert set(rec["members"]) == {"a", "b"}
        assert rec["best_member"] in {"a", "b"}
        assert 0.0 <= rec["obs_growth_mean_burn_fraction"] <= 1.0

    def test_report_aggregation(self, case):
        rec = run_fire_day(case, FAST)
        rows = flatten([rec])
        assert {r["window"] for r in rows} == {"4h", "8h", "oracle"}
        s = summarize([r for r in rows if r["window"] == "8h"])
        assert s["f1"]["n"] == 1
        assert per_fire_means(rows)[0]["fire_id"] == "2016_106"


class TestPieces:
    def test_initial_state_perimeter_and_bennett(self, fixture):
        dom, _, _, day = fixture
        outline, others, extra = initial_state(dom, day, "perimeter")
        assert len(outline) >= 4 and extra is None
        _, ign, nonfuel = initial_state(dom, day, "bennett")
        # Bennett ignites only day-198 cells touching day-199 growth; older burn is non-fuel
        assert 0 < len(ign) <= 21
        assert nonfuel.sum() == 47 + 76 + 21 - len(ign)

    def test_crop_for_day_keeps_burned_area(self, fixture):
        dom, _, _, day = fixture
        c = crop_for_day(dom, day, margin_m=500.0)
        assert int((c.dob == day).sum()) == 428
        assert c.rows < dom.rows and c.cols < dom.cols

    def test_seasonal_fuel(self, fixture):
        dom = fixture[0]
        small = dom.crop(0, 2, 0, 2)
        small.fuel[:] = 108  # D-1 in the 2014b scheme
        assert fuel_grid_for(small, 120, RunOptions()).fuel_types[0][0] is FuelType.D1
        assert fuel_grid_for(small, 200, RunOptions()).fuel_types[0][0] is FuelType.D2
        assert fuel_grid_for(small, 280, RunOptions()).fuel_types[0][0] is FuelType.D1

    def test_terrain_on_simulation_grid(self, fixture):
        dom = fixture[0]
        t = terrain_grid_for(dom)
        assert (t.rows, t.cols) == (dom.rows, dom.cols)
        assert t.slope[40][40] == pytest.approx(2.0, rel=1e-3)
        assert t.aspect[40][40] == pytest.approx(180.0)  # upslope toward the south

    def test_member_apply(self):
        h = (HourlyWeather(0.0, 20, 30, 10, 350), HourlyWeather(1.0, 20, 30, 20, 20))
        out = Member(wind_direction_offset=20, wind_speed_factor=1.5).apply(h)
        assert [x.wind_direction for x in out] == [10.0, 40.0]
        assert [x.wind_speed for x in out] == [15.0, 30.0]
        assert len(wind_direction_members()) == 12

    def test_save_load_roundtrip(self, fixture, tmp_path):
        dom = fixture[0]
        dom.save(tmp_path / "d.npz")
        back = FireDomain.load(tmp_path / "d.npz")
        assert np.array_equal(back.dob, dom.dob) and back.lat_max == dom.lat_max

    def test_parse_open_meteo_and_burn_hours(self):
        data = {"hourly": {
            "time": ["2016-07-17T05:00", "2016-07-17T06:00", "2016-07-17T07:00"],
            "temperature_2m": [10, 12, None], "relative_humidity_2m": [80, 70, 60],
            "wind_speed_10m": [5, 6, 7], "wind_direction_10m": [180, 190, 200],
            "precipitation": [0, 0, 0]}}
        recs = parse_open_meteo(data)
        assert len(recs) == 2  # hour with a missing value dropped
        hw = burn_hours(recs, datetime(2016, 7, 17, 6), 1)
        assert hw[0].wind_direction == 190 and hw[0].hours_from_start == 0.0
        with pytest.raises(KeyError):
            burn_hours(recs, datetime(2016, 7, 17, 6), 2)

    def test_read_groups(self, tmp_path):
        p = tmp_path / "g.csv"
        p.write_text("ID,DOB,ffmc,dmc\n2016_1,150,90.5,30\n2016_1,151,91,NA\n")
        g = read_groups(p)
        assert g["2016_1"][150]["ffmc"] == 90.5 and math.isnan(g["2016_1"][151]["dmc"])

    def test_growth_class(self):
        assert growth_class(50) == "<100 ha" and growth_class(20000) == ">10,000 ha"

"""Pre-fire (back-cast) fuel for dated historical scenarios (firesim.data.backcast).

Synthetic rasters on a small "Alberta grid" (EPSG:3400, 100 m): grass O-1 everywhere, a 2011
burn (restore key 2011209, i.e. ended 28 July 2011) and a 2023 burn with no end date (2023366).
The 2010 and 2020 "SCANFI epochs" type the burned cells C-2 and M-1 50 %.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform

from firesim.data.backcast import (Backcast, BackcastData, apply_on_lattice, compose, plan,
                                   scenario_key)
from firesim.data.provincial import FuelSources, Lattice, fuel_counts, provincial_window
from firesim.fbp.constants import FuelType

LAT0, LNG0 = 55.25, -114.7
W, H = 200, 200


def _origin():
    x, y = transform("EPSG:4326", "EPSG:3400", [LNG0], [LAT0])
    return round(x[0] / 100) * 100 - 10_000.0, round(y[0] / 100) * 100 + 10_000.0


def _write(path, arr, dtype, nodata=None):
    x0, y0 = _origin()
    with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=1, dtype=dtype,
                       crs=CRS.from_epsg(3400), transform=from_origin(x0, y0, 100.0, 100.0),
                       nodata=nodata) as dst:
        dst.write(arr.astype(dtype), 1)


def build(d, epochs=(2010, 2020)):
    """AB grid + back-cast data in ``d``; returns (ab path, data dir)."""
    ab = np.full((H, W), 31, np.int16)
    ab[:, :20] = 0  # outside Alberta
    key = np.zeros((H, W), np.int32)
    key[80:120, 80:120] = 2011209       # 2011 fire, ended 28 July
    key[80:120, 140:180] = 2023366      # 2023 fire, no end date
    key[80:120, :20] = 2023366          # burn outside the Alberta extent: never restored
    _write(d / "ab.tif", ab, "int16")
    bc = d / "bc"
    bc.mkdir()
    _write(bc / "key.tif", key, "int32", nodata=0)
    man = {"version": 1, "grid_vintage_year": 2025, "burns": {"file": "key.tif"}, "epochs": {}}
    for e in epochs:
        pre = np.zeros((H, W), np.int16)
        pre[key > (e + 1) * 1000] = 2 if e == 2010 else 650
        if e == 2010:
            pre[80:120, 140:150] = 0  # a strip with no pre-fire value
        _write(bc / f"e{e}.tif", pre, "int16", nodata=0)
        man["epochs"][str(e)] = f"e{e}.tif"
    (bc / "backcast.json").write_text(json.dumps(man))
    return str(d / "ab.tif"), str(bc)


def point(col, row):
    x0, y0 = _origin()
    lng, lat = transform("EPSG:3400", "EPSG:4326", [x0 + (col + 0.5) * 100],
                         [y0 - (row + 0.5) * 100])
    return lat[0], lng[0]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    ab, bc = build(tmp_path_factory.mktemp("backcast"))
    return {"ab": ab, "bc": BackcastData.load(bc), "dir": bc}


class TestKeysAndPlan:
    def test_scenario_key(self):
        assert scenario_key(date(2011, 5, 14)) == 2011134
        assert scenario_key(datetime(2011, 12, 31, 23, 0)) == 2011365
        tz = timezone(timedelta(hours=-6))
        assert scenario_key(datetime(2011, 5, 14, 17, 50, tzinfo=tz)) == 2011134

    def test_load_and_epochs(self, data):
        bc = data["bc"]
        assert [e for e, _ in bc.epochs] == [2010, 2020] and bc.vintage_year == 2025
        assert bc.epoch_for(2011)[0] == 2010
        assert bc.epoch_for(2020)[0] == 2010  # strictly before the scenario's year
        assert bc.epoch_for(2021)[0] == 2020
        assert bc.epoch_for(2010) is None

    def test_load_missing(self, tmp_path):
        assert BackcastData.load(None) is None
        assert BackcastData.load(str(tmp_path)) is None

    def test_plan(self, data):
        assert plan(data["bc"], None) == (None, None)
        assert plan(data["bc"], date(2026, 6, 1)) == (None, None)  # after the vintage year
        bc, info = plan(data["bc"], date(2011, 5, 14))
        assert isinstance(bc, Backcast) and bc.epoch[0] == 2010
        assert info == {"applied": True, "as_of": "2011-05-14"}
        bc, info = plan(data["bc"], date(2025, 5, 1))
        assert bc.epoch[0] == 2020 and info["applied"]
        bc, info = plan(data["bc"], date(2009, 7, 1))
        assert bc is None and not info["applied"] and "2010" in info["reason"]
        assert info["note"].startswith("Pre-fire fuel not applied for 2009-07-01")
        bc, info = plan(None, date(2011, 5, 14))
        assert bc is None and "not installed" in info["reason"]


class TestCompose:
    def test_rule(self):
        base = np.array([31, 31, 31, 31, 105, 0, 32767], np.int32)
        key = np.array([2011209, 2011100, 2023366, 0, 2012366, 2023366, 2023366], np.int32)
        pre = np.array([2, 2, 650, 2, 13, 2, 2], np.int32)
        out, restored, unrestored = compose(base, key, pre, scenario_key(date(2011, 5, 14)))
        # restored: ended after the scenario, a later year; not: ended before it, never burned,
        # no-data / outside cells of the base grid
        assert out.tolist() == [2, 31, 650, 31, 13, 0, 32767]
        assert restored.tolist() == [True, False, True, False, True, False, False]
        assert not unrestored.any()

    def test_same_year_after_the_fire_is_not_restored(self):
        out, restored, _ = compose(np.array([31]), np.array([2011209]), np.array([2]),
                                   scenario_key(date(2011, 8, 1)))
        assert out[0] == 31 and not restored[0]

    def test_scenario_on_the_end_day_is_restored(self):
        out, _, _ = compose(np.array([31]), np.array([2011209]), np.array([2]), 2011209)
        assert out[0] == 2

    def test_unrestored_without_prefire_value(self):
        out, restored, unrestored = compose(np.array([31]), np.array([2023366]), np.array([0]),
                                            2011134)
        assert out[0] == 31 and not restored[0] and unrestored[0]


class TestWindow:
    def _src(self, data, **kw):
        return FuelSources(ab_path=data["ab"], window_km=16.0, **kw)

    def test_window_restores_burns_after_the_scenario(self, data):
        lat, lng = point(100, 100)
        bc, _ = plan(data["bc"], date(2011, 5, 14))
        plain = provincial_window(self._src(data), lat, lng)
        back = provincial_window(self._src(data, backcast=bc), lat, lng)
        assert plain.backcast is None
        assert set(fuel_counts(plain.grid)) == {"O1b"}
        counts = fuel_counts(back.grid)
        assert counts["C2"] > 0 and counts["O1b"] > counts["C2"]
        st = back.backcast
        assert st["epoch"] == 2010 and 0 < st["restored_share"] < 0.5
        assert st["unrestored_share"] > 0  # the strip with no pre-fire value
        assert st["restored_before"] == {"O-1": 1.0} and st["restored_after"] == {"C": 1.0}
        # the 2011 fire's own scar and the 2023 scar are both restored (C-2 from 2010)
        g = back.grid
        lat_c, lng_c = point(100, 100)
        lat_l, lng_l = point(160, 100)
        lt = Lattice.of_grid(g)
        r, c = lt.cell_of(lat_c, lng_c)
        assert g.fuel_types[r][c] is FuelType.C2
        r, c = lt.cell_of(lat_l, lng_l)
        assert g.fuel_types[r][c] is FuelType.C2

    def test_later_scenario_restores_only_later_burns(self, data):
        lat, lng = point(130, 100)
        bc, _ = plan(data["bc"], date(2021, 6, 1))
        g = provincial_window(self._src(data, backcast=bc), lat, lng).grid
        lt = Lattice.of_grid(g)
        r, c = lt.cell_of(*point(100, 100))  # 2011 burn: before the scenario, unchanged
        assert g.fuel_types[r][c] is FuelType.O1b
        r, c = lt.cell_of(*point(160, 100))  # 2023 burn: M-1 50 % from the 2020 epoch
        assert g.fuel_types[r][c] is FuelType.M1 and g.pc[r][c] == 50.0

    def test_backcast_changes_the_cache_key(self, data):
        bc, _ = plan(data["bc"], date(2011, 5, 14))
        assert self._src(data) != self._src(data, backcast=bc)
        assert hash(self._src(data, backcast=bc)) == hash(self._src(data, backcast=bc))

    def test_apply_on_lattice_reads_aligned_cells(self, data):
        lat, lng = point(100, 100)
        lattice = Lattice.around(lat, lng, 16.0, 50.0)
        from firesim.data.provincial import read_codes_on_lattice
        ab = read_codes_on_lattice(data["ab"], lattice)
        bc, _ = plan(data["bc"], date(2011, 5, 14))
        codes, st = apply_on_lattice(bc, lattice, ab)
        key = read_codes_on_lattice(bc.data.burn_key, lattice)
        changed = codes != ab
        assert changed.any() and (key[changed] >= 2011134).all()
        assert ((codes == 2) == changed).all()

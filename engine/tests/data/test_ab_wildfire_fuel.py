"""Alberta Wildfire FBP fuel grid (2025, 100 m) as the default fuel outside Edmonton.

Covers: the code table for every row of the service's raster attribute table, percent conifer
from the M-1/M-2 codes, the leaf-on / leaf-off rule, windowed reads of a large raster, the
source choice (inside / straddling / outside the City grid / outside Alberta), filling the City
grid's no-data cells, the 20 m window source, and an engine sanity check that O-1, C-2,
M-1/M-2 by percent conifer and D-1/D-2 by date spread in the expected order.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform

from firesim.data.fine_grid import FineFuelSource
from firesim.data.fuel_loader import (
    AB_WILDFIRE_CODES,
    CODE_SCHEMES,
    _detect_code_map,
    load_fuel_grid,
    percent_conifer_for_code,
)
from firesim.data.provincial import (
    FuelSources,
    Lattice,
    choose_fuel,
    code_at,
    provincial_window,
    read_codes_on_lattice,
    stack_sources,
)
from firesim.fbp.constants import FuelType
from firesim.fbp.season import GREENUP_DOY, LEAFOFF_DOY, apply_season, is_leaf_on, seasonal_fuel
from firesim.spread.huygens import FuelGrid
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

# Every row of the service's raster attribute table (rat.json, downloaded 2026-10-10):
# VALUE, FUEL_TYPE
RAT = [
    (1, "C-1"), (2, "C-2"), (3, "C-3"), (4, "C-4"), (5, "C-5"), (6, "C-6"), (7, "C-7"),
    (13, "D-1/D-2"), (21, "S-1"), (22, "S-2"), (23, "S-3"), (31, "O-1"),
    (101, "Non-Fuel"), (102, "Water"), (105, "Vegetated Non-Fuel"),
    (610, "M-1/M-2 (10 PC)"), (615, "M-1/M-2 (15 PC)"), (620, "M-1/M-2 (20 PC)"),
    (625, "M-1/M-2 (25 PC)"), (630, "M-1/M-2 (30 PC)"), (635, "M-1/M-2 (35 PC)"),
    (640, "M-1/M-2 (40 PC)"), (645, "M-1/M-2 (45 PC)"), (650, "M-1/M-2 (50 PC)"),
    (655, "M-1/M-2 (55 PC)"), (660, "M-1/M-2 (60 PC)"), (665, "M-1/M-2 (65 PC)"),
    (670, "M-1/M-2 (70 PC)"), (675, "M-1/M-2 (75 PC)"), (680, "M-1/M-2 (80 PC)"),
    (685, "M-1/M-2 (85 PC)"), (690, "M-1/M-2 (90 PC)"), (104, "Unclassified"),
]

EXPECTED = {
    "C-1": FuelType.C1, "C-2": FuelType.C2, "C-3": FuelType.C3, "C-4": FuelType.C4,
    "C-5": FuelType.C5, "C-6": FuelType.C6, "C-7": FuelType.C7,
    "D-1/D-2": FuelType.D1,  # leafless label; the run picks D-2 when leaves are on
    "S-1": FuelType.S1, "S-2": FuelType.S2, "S-3": FuelType.S3,
    "O-1": FuelType.O1b,  # standing grass (docs/fuel-grid-alberta.md section 3)
    "Non-Fuel": None, "Water": None, "Vegetated Non-Fuel": None, "Unclassified": None,
}


# ── Code table ──────────────────────────────────────────────────────────────────────────────


class TestCodeTable:
    def test_rat_has_33_rows_all_mapped(self):
        assert len(RAT) == 33
        for code, _ in RAT:
            assert code in AB_WILDFIRE_CODES, code

    @pytest.mark.parametrize("code,label", RAT)
    def test_every_rat_code(self, code, label):
        ft = AB_WILDFIRE_CODES[code]
        if label.startswith("M-1/M-2"):
            assert ft is FuelType.M1
            assert percent_conifer_for_code(code, "ab_wildfire") == float(label[9:11])
        else:
            assert ft is EXPECTED[label]
            assert percent_conifer_for_code(code, "ab_wildfire") is None

    @pytest.mark.parametrize("code", [0, 32767, -9999, -1])
    def test_no_data_codes(self, code):
        assert AB_WILDFIRE_CODES[code] is None

    def test_no_codes_beyond_the_rat(self):
        rat = {c for c, _ in RAT} | {0, 32767, -9999, -1}
        assert set(AB_WILDFIRE_CODES) == rat

    def test_percent_conifer_other_schemes(self):
        assert percent_conifer_for_code(650, "cfs_national") == 50.0
        assert percent_conifer_for_code(425, "cfs_national") == 25.0
        assert percent_conifer_for_code(835, "cfs_national") is None  # percent dead fir
        assert percent_conifer_for_code(650, "canopy_lidar") is None
        assert percent_conifer_for_code(600, "ab_wildfire") is None
        assert percent_conifer_for_code(695, "ab_wildfire") is None

    def test_explicit_only(self):
        # 31 is O-1a in the CIFFC scheme and grass in other local schemes: never auto-detected
        assert _detect_code_map({2, 13, 31, 650}) is not AB_WILDFIRE_CODES
        assert _detect_code_map({2, 13, 31, 650}, "ab_wildfire") is AB_WILDFIRE_CODES
        assert CODE_SCHEMES["ab_wildfire"] is AB_WILDFIRE_CODES


# ── Season ──────────────────────────────────────────────────────────────────────────────────


class TestSeason:
    def test_dates(self):
        assert (GREENUP_DOY, LEAFOFF_DOY) == (150, 258)
        assert not is_leaf_on(149) and is_leaf_on(150) and is_leaf_on(257)
        assert not is_leaf_on(258) and not is_leaf_on(None)

    def test_harness_uses_the_same_dates(self):
        from firesim.validation.harness import RunOptions

        assert (RunOptions().greenup_doy, RunOptions().leafoff_doy) == (GREENUP_DOY, LEAFOFF_DOY)

    @pytest.mark.parametrize("ft,doy,out", [
        (FuelType.D1, 200, FuelType.D2), (FuelType.D1, 100, FuelType.D1),
        (FuelType.M1, 200, FuelType.M2), (FuelType.M1, 300, FuelType.M1),
        (FuelType.M3, 200, FuelType.M4), (FuelType.D2, 100, FuelType.D2),
        (FuelType.C2, 200, FuelType.C2), (None, 200, None), (FuelType.D1, None, FuelType.D1),
    ])
    def test_seasonal_fuel(self, ft, doy, out):
        assert seasonal_fuel(ft, doy) is out

    def test_apply_season_copies(self):
        g = FuelGrid([[FuelType.D1, FuelType.M1], [FuelType.C2, None]], 0, 1, 0, 1, 2, 2,
                     pc=[[None, 40.0], [None, None]])
        assert apply_season(g, 100) is g
        green = apply_season(g, 180)
        assert green is not g and g.fuel_types[0][0] is FuelType.D1  # cached grid untouched
        assert green.fuel_types == [[FuelType.D2, FuelType.M2], [FuelType.C2, None]]
        assert green.pc == g.pc


# ── Synthetic rasters ─────────────────────────────────────────────────────────────────────────

# A 30 x 20 km "Alberta grid" (EPSG:3400, 100 m) around Edmonton-like coordinates; its western
# 8 km is 0 (outside the service extent, i.e. "outside Alberta").
LAT0, LNG0 = 53.5, -113.5
AB_W, AB_H = 300, 200


def _ab_origin():
    x, y = transform("EPSG:4326", "EPSG:3400", [LNG0], [LAT0])
    return round(x[0] / 100) * 100 - 15_000.0, round(y[0] / 100) * 100 + 10_000.0


def _ab_codes():
    a = np.full((AB_H, AB_W), 31, dtype=np.int16)  # O-1 everywhere
    a[:, :80] = 0  # outside Alberta
    a[:, 150:170] = 650  # M-1/M-2 50 PC
    a[:, 170:180] = 13  # D-1/D-2
    a[:, 180:190] = 2  # C-2
    a[:, 190:195] = 105  # vegetated non-fuel
    a[:, 195:200] = 102  # water
    a[0:5, :] = np.where(a[0:5, :] == 0, 0, 32767)  # no-data strip inside the province
    return a


@pytest.fixture(scope="module")
def rasters(tmp_path_factory):
    d = tmp_path_factory.mktemp("abgrid")
    x0, y0 = _ab_origin()
    ab = d / "ab.tif"
    with rasterio.open(ab, "w", driver="GTiff", height=AB_H, width=AB_W, count=1, dtype="int16",
                       crs=CRS.from_epsg(3400), transform=from_origin(x0, y0, 100.0, 100.0),
                       tiled=True, blockxsize=128, blockysize=128) as dst:
        dst.write(_ab_codes(), 1)
    # "City grid": 6 x 6 km of C-2 at 20 m (canopy_lidar codes), EPSG:3776, centred east of
    # the provincial border; its eastern 1 km is 0 (no data) and a 200 m strip is 99 non-fuel.
    cx, cy = transform("EPSG:4326", "EPSG:3776", [LNG0 + 0.03], [LAT0])
    city = d / "city.tif"
    c = np.full((300, 300), 2, dtype=np.int32)
    c[:, 250:] = 0
    c[:, 100:110] = 99
    with rasterio.open(city, "w", driver="GTiff", height=300, width=300, count=1, dtype="int32",
                       nodata=0, crs=CRS.from_epsg(3776),
                       transform=from_origin(cx[0] - 3000, cy[0] + 3000, 20.0, 20.0)) as dst:
        dst.write(c, 1)
    return {"ab": str(ab), "city": str(city), "dir": d}


def _ab_point(col, row):
    """lat, lng of the centre of Alberta-grid cell (row, col)."""
    x0, y0 = _ab_origin()
    lng, lat = transform("EPSG:3400", "EPSG:4326", [x0 + (col + 0.5) * 100], [y0 - (row + 0.5) * 100])
    return lat[0], lng[0]


# ── Windowed reads ────────────────────────────────────────────────────────────────────────────


class TestWindowedRead:
    def test_point_codes(self, rasters):
        lat, lng = _ab_point(160, 100)
        assert code_at(rasters["ab"], lat, lng) == 650
        lat, lng = _ab_point(10, 100)
        assert code_at(rasters["ab"], lat, lng) == 0
        assert code_at(rasters["ab"], 60.0, -100.0) is None  # outside the raster

    def test_window_matches_point_lookup(self, rasters):
        lat_c, lng_c = _ab_point(160, 100)
        lat = Lattice.around(lat_c, lng_c, size_km=4.0, cell_m=50.0, snap_km=0.1)
        codes = read_codes_on_lattice(rasters["ab"], lat)
        assert codes.shape == (80, 80)
        rng = np.random.default_rng(1)
        for r, c in rng.integers(0, 80, size=(40, 2)):
            clat = lat.lat_max - (r + 0.5) * lat.dlat
            clng = lat.lng_min + (c + 0.5) * lat.dlng
            assert codes[r, c] == code_at(rasters["ab"], clat, clng)

    def test_window_reads_only_the_window(self, rasters, monkeypatch):
        """The source read is bounded by the window (+2 cells), not the whole raster."""
        shapes = []
        orig = rasterio.io.DatasetReader.read

        def spy(self, *a, **k):
            out = orig(self, *a, **k)
            shapes.append(out.shape)
            return out

        monkeypatch.setattr(rasterio.io.DatasetReader, "read", spy)
        lat_c, lng_c = _ab_point(200, 100)
        read_codes_on_lattice(rasters["ab"], Lattice.around(lat_c, lng_c, 2.0, 50.0, 0.1))
        assert shapes and max(s[-1] for s in shapes) < 40 and max(s[-2] for s in shapes) < 40

    def test_window_outside_raster_is_nodata(self, rasters):
        codes = read_codes_on_lattice(rasters["ab"], Lattice.around(60.0, -100.0, 2.0, 50.0))
        assert (codes == -9999).all()

    def test_refinement_is_nearest(self, rasters):
        """100 m -> 50 m keeps only the source classes (no invented classes at boundaries)."""
        lat_c, lng_c = _ab_point(175, 100)
        codes = read_codes_on_lattice(rasters["ab"], Lattice.around(lat_c, lng_c, 6.0, 50.0, 0.1))
        assert set(np.unique(codes)) <= {31, 650, 13, 2, 105, 102}


# ── Source choice and stacking ───────────────────────────────────────────────────────────────


class TestSelection:
    def _sources(self, rasters, **kw):
        return FuelSources(ab_path=rasters["ab"], city_path=rasters["city"], window_km=4.0,
                           city_margin_km=1.0, **kw)

    def test_inside_city(self, rasters):
        assert choose_fuel(self._sources(rasters), LAT0, LNG0 + 0.03).kind == "city"

    def test_near_city_edge_is_window(self, rasters):
        # 0.5 km inside the City box: closer than the 1 km margin -> window with both grids
        src = self._sources(rasters)
        lat, lng = LAT0, LNG0 + 0.03 + (2500 / (111320 * math.cos(math.radians(LAT0))))
        assert choose_fuel(src, lat, lng).kind == "provincial"
        st = provincial_window(src, lat, lng)
        assert st.share["city"] > 0.1 and st.share["ab_wildfire"] > 0.1

    def test_outside_city_in_alberta(self, rasters):
        lat, lng = _ab_point(220, 100)
        ch = choose_fuel(self._sources(rasters), lat, lng)
        assert ch.kind == "provincial" and "Open Government Licence – Alberta" in ch.attribution
        st = provincial_window(self._sources(rasters), lat, lng)
        assert "city" not in st.share and st.share["ab_wildfire"] == 1.0

    def test_outside_alberta_uniform(self, rasters):
        lat, lng = _ab_point(20, 100)
        ch = choose_fuel(self._sources(rasters), lat, lng, uniform_fuel="C3")
        assert ch.kind == "uniform" and "C3" in ch.label
        assert choose_fuel(self._sources(rasters), 60.0, -100.0).kind == "uniform"

    def test_outside_alberta_national(self, rasters):
        # a "national" grid (CIFFC codes) covering the same box, all C-3
        d = rasters["dir"]
        with rasterio.open(rasters["ab"]) as src:
            prof = src.profile
        nat = d / "nat.tif"
        with rasterio.open(nat, "w", **prof) as dst:
            dst.write(np.full((AB_H, AB_W), 3, dtype=np.int16), 1)
        src = self._sources(rasters, national_path=str(nat))
        lat, lng = _ab_point(70, 100)  # 1 km west of the provincial border
        assert choose_fuel(src, lat, lng).kind == "national"
        st = provincial_window(src, lat, lng)
        assert st.share["national"] > 0.3 and st.share["ab_wildfire"] > 0.1
        assert st.uncovered == 0.0

    def test_no_data_strip_inside_alberta_counts_as_alberta(self, rasters):
        lat, lng = _ab_point(220, 2)  # 32767 strip
        assert choose_fuel(self._sources(rasters), lat, lng).kind == "provincial"

    def test_stack_priority(self):
        lat = Lattice(1.0, 0.0, 0.1, 0.1, 1, 5)
        city = np.array([[2, 99, -9999, -9999, -9999]])
        ab = np.array([[31, 31, 650, 0, 32767]])
        st = stack_sources([("city", city, "canopy_lidar"), ("ab_wildfire", ab, "ab_wildfire")], lat)
        assert st.grid.fuel_types == [[FuelType.C2, None, FuelType.M1, None, None]]
        assert st.grid.pc == [[None, None, 50.0, None, None]]
        assert st.share == {"city": 0.4, "ab_wildfire": 0.2} and st.uncovered == pytest.approx(0.4)


class TestCityFill:
    def test_fill_keeps_every_city_cell(self, rasters):
        g0 = load_fuel_grid(rasters["city"])
        g1 = load_fuel_grid(rasters["city"], fill_path=rasters["ab"])
        assert (g0.rows, g0.cols) == (g1.rows, g1.cols)
        filled = 0
        for r in range(g0.rows):
            for c in range(g0.cols):
                if g0.fuel_types[r][c] is not None:
                    assert g1.fuel_types[r][c] is g0.fuel_types[r][c]
                elif g1.fuel_types[r][c] is not None:
                    filled += 1
        assert filled > 0  # the City grid's no-data strip now carries Alberta fuel

    def test_mapped_non_fuel_is_not_filled(self, rasters):
        g1 = load_fuel_grid(rasters["city"], fill_path=rasters["ab"])
        g0 = load_fuel_grid(rasters["city"])
        lat_mid = (g0.lat_min + g0.lat_max) / 2
        # a cell in the 99 strip (columns 100-110 of 300 = 35 % of the width)
        lng = g0.lng_min + 0.35 * (g0.lng_max - g0.lng_min)
        assert g0.get_fuel_at(lat_mid, lng) is None and g1.get_fuel_at(lat_mid, lng) is None

    def test_fine_source_fill_and_season(self, rasters):
        fine = FineFuelSource.from_raster(rasters["city"], fill_path=rasters["ab"])
        assert FuelType.C2 in fine.types and FuelType.O1b in fine.types
        crop = fine.crop(fine.bounds)
        assert any(ft is FuelType.O1b for row in crop.fuel_types for ft in row)
        green = fine.with_season(200)
        assert green.idx is fine.idx


# ── Engine sanity ─────────────────────────────────────────────────────────────────────────────


def _uniform_ab(tmp_path, code):
    x0, y0 = _ab_origin()
    p = tmp_path / f"ab_{code}.tif"
    with rasterio.open(p, "w", driver="GTiff", height=60, width=60, count=1, dtype="int16",
                       crs=CRS.from_epsg(3400), transform=from_origin(x0, y0, 100.0, 100.0)) as dst:
        dst.write(np.full((60, 60), code, dtype=np.int16), 1)
    return str(p)


def _area(tmp_path, code, doy, hours=1.0, bui_high=True):
    path = _uniform_ab(tmp_path, code)
    lat, lng = _ab_point(30, 30)
    st = provincial_window(FuelSources(ab_path=path, window_km=4.0), lat, lng)
    grid = apply_season(st.grid, doy)
    cfg = SimulationConfig(
        ignition_lat=lat, ignition_lng=lng,
        weather=WeatherInput(temperature=25, relative_humidity=25, wind_speed=20,
                             wind_direction=270, precipitation_24h=0),
        duration_hours=hours, snapshot_interval_minutes=60, ffmc=91,
        dmc=60 if bui_high else 20, dc=400 if bui_high else 100, grass_cure=90, day_of_year=doy,
        percent_conifer=50.0,
    )
    frames = list(Simulator(cfg, fuel_grid=grid).run())
    return frames[-1].area_ha, grid


class TestEngineSanity:
    def test_grass_is_standing_and_fastest(self, tmp_path):
        a_o1, g = _area(tmp_path, 31, 200)
        a_c2, _ = _area(tmp_path, 2, 200)
        assert g.fuel_types[0][0] is FuelType.O1b
        assert a_o1 > a_c2 > 0

    def test_mixedwood_percent_conifer_orders_spread(self, tmp_path):
        a20, g20 = _area(tmp_path, 620, 120)
        a50, _ = _area(tmp_path, 650, 120)
        a80, _ = _area(tmp_path, 680, 120)
        assert g20.pc[0][0] == 20.0 and g20.fuel_types[0][0] is FuelType.M1
        assert a20 < a50 < a80

    def test_mixedwood_pc_layer_equals_run_value(self, tmp_path):
        """Per-cell 50 % equals the run's percent_conifer of 50 % (the pc layer is the only
        difference)."""
        a, g = _area(tmp_path, 650, 120)
        import dataclasses

        lat, lng = _ab_point(30, 30)
        cfg = SimulationConfig(
            ignition_lat=lat, ignition_lng=lng,
            weather=WeatherInput(25, 25, 20, 270, 0), duration_hours=1.0,
            snapshot_interval_minutes=60, ffmc=91, dmc=60, dc=400, grass_cure=90,
            day_of_year=120, percent_conifer=50.0)
        b = list(Simulator(cfg, fuel_grid=dataclasses.replace(g, pc=None)).run())[-1].area_ha
        assert a == pytest.approx(b)

    def test_aspen_by_date(self, tmp_path):
        a_off, g_off = _area(tmp_path, 13, 120, bui_high=False)  # 30 April: D-1
        a_on, g_on = _area(tmp_path, 13, 200, bui_high=False)  # 19 July: D-2 (BUI < 80)
        assert g_off.fuel_types[0][0] is FuelType.D1 and g_on.fuel_types[0][0] is FuelType.D2
        assert a_off > a_on

    def test_mixedwood_by_date(self, tmp_path):
        a_off, g_off = _area(tmp_path, 650, 120)
        a_on, g_on = _area(tmp_path, 650, 200)
        assert g_off.fuel_types[0][0] is FuelType.M1 and g_on.fuel_types[0][0] is FuelType.M2
        assert a_off > a_on


class TestGrassAndVegetatedNonFuelDecisions:
    """The 2026-10-12 decisions ([R35], docs/fuel-grid-alberta.md section 3): Alberta code 31
    stays O-1b (standing) all year and 105 stays non-fuel, and the FBP facts the O-1 decision
    rests on: both grass types share the curing factor, O-1a is faster below ISI 8 and O-1b
    faster from ISI 8 up (by about 20 % at ISI 20 and 33 % at ISI 60)."""

    def test_codes_and_no_season_swap_for_grass(self):
        from firesim.fbp.season import seasonal_fuel

        assert AB_WILDFIRE_CODES[31] is FuelType.O1b
        assert AB_WILDFIRE_CODES[105] is None
        for doy in (None, 75, 120, 200, 300):  # spring window, summer, fall, undated
            assert seasonal_fuel(FuelType.O1b, doy) is FuelType.O1b
            assert seasonal_fuel(FuelType.O1a, doy) is FuelType.O1a

    @staticmethod
    def _rsi(ft, isi, cure=95.0):
        from firesim.fbp.calculator import calculate_rsi
        from firesim.fbp.constants import FUEL_TYPES

        return calculate_rsi(FUEL_TYPES[ft], isi, grass_cure=cure)

    def test_matted_vs_standing_crossover(self):
        assert self._rsi(FuelType.O1a, 5) > self._rsi(FuelType.O1b, 5)
        assert self._rsi(FuelType.O1a, 7.5) > self._rsi(FuelType.O1b, 7.5)
        for isi in (8, 10, 20, 40, 60):
            assert self._rsi(FuelType.O1b, isi) >= self._rsi(FuelType.O1a, isi)
        assert self._rsi(FuelType.O1b, 20) / self._rsi(FuelType.O1a, 20) == pytest.approx(1.21, abs=0.01)
        assert self._rsi(FuelType.O1b, 60) / self._rsi(FuelType.O1a, 60) == pytest.approx(1.34, abs=0.01)

    def test_ratio_independent_of_curing(self):
        for cure in (60.0, 80.0, 95.0, 100.0):
            r = self._rsi(FuelType.O1b, 30, cure) / self._rsi(FuelType.O1a, 30, cure)
            assert r == pytest.approx(self._rsi(FuelType.O1b, 30, 100.0) / self._rsi(FuelType.O1a, 30, 100.0))

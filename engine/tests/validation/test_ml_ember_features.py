"""Feature builder of the ML ember-cast data infrastructure (PREREG_ML_EMBER_DEV sections 3, 4, 14).

Synthetic-fire tests (no data pool): (c) every column is in the section-4 contract (hard-coded list); (d) no feature
changes when epoch i's detections are perturbed; (e) FRP uses epoch i-1 only; (f) box coordinates and texture names
are absent from the cell tables; (g) negative-subsampling counts and seed reproducibility; the sibling FBP and weather
functions equal the frozen v8 primitives; the final perimeter enters only the control column. Pool-dependent
(skipped without the pool): (h) recomputed geometry equals the v8 npz exactly on five training fires."""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

for _m in ("pandas", "scipy", "shapely", "pyproj", "geopandas"):
    pytest.importorskip(_m)
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "src"))
F = importlib.import_module("scripts.ml_ember.features")
B = importlib.import_module("scripts.ml_ember.build_table")
P = importlib.import_module("scripts.ml_ember.paths")
Iv = importlib.import_module("scripts.validation.v8.intervals")

# PREREG_ML_EMBER_DEV section 4, column names exactly as frozen (the ONLY features any rung may use)
CONTRACT = {
    "d_act_km", "d_all_km", "sec_act", "sec_all", "bear_off_deg", "act_len_km", "act_share", "fire_area_km2", "fire_age_h",
    "gap_h", "hour_local", "u10_ms", "wd_deg", "m_ff_pct", "ffmc", "temp_c", "rh_pct", "stn_dist_km", "I_head_kwm",
    "ros_mmin", "cfb", "spread_prev_km", "frp_prev_mw", "fuel_code", "fuel_group", "hazard", "hazshare", "curing_pct",
    "elev_m", "slope_pct", "aspect_deg", "tpi_1km", "d_nonfuel_km", "d_water_km", "d_act_x_fuel_group", "m8_log", "b1",
    "region", "year", "sat_era", "fuel_restored_share", "perim_agree_flag", "gap_class",
    # M8 sufficient statistics stored so that m8_log can be refit per fold (section 4 "A, u10, m_ff, fit.py"); frozen reference
    "A_s0", "A_s1", "A_s2", "A_l0", "A_l1", "A_l2", "zone", "m8_log_frozen",
}
IDS = {"row_id", "fire_key", "epoch", "y", "w_sample"}
FORBIDDEN = {"bi", "bj", "row", "col", "x_box", "y_box", "code_entropy_1km", "patch_size", "edge_density_1km", "in_final",
             "gap_class", "n_jumps", "frp_end", "out_final_m", "fire_index"}
POOL_PRESENT = (P.POOL / "fires").exists() and (P.PREFREEZE / "results" / "split.csv").exists()
VERIFY_FIRES = ["NBAC:2018_1414", "NBAC:2018_1457", "NBAC:2018_2184", "MTBS:AZ3137511119220220612", "MTBS:AZ3279011092620240712"]


# ── synthetic fire ───────────────────────────────────────────────────────────────────────────────

def synthetic(frp_scale=None, shift_epoch=None, big_perimeter=False, seed=7):
    """A 150 x 150 cell (125 m) C-2 landscape with a water strip and a non-fuel strip; four VIIRS epochs 6 h apart
    advancing east under a west wind; the last epoch holds a far 'jump' detection. ``frp_scale`` = {epoch: factor},
    ``shift_epoch`` = {epoch: cells} moves that epoch's detections east."""
    from pyproj import Transformer
    rng = np.random.default_rng(seed)
    nx = ny = 150; cs = 125.0; x0, y1 = 500000.0, 5600000.0
    g = dict(crs="EPSG:32612", x0=x0, y1=y1, nx=nx, ny=ny, cell=cs, span_km=10.0)
    code = np.full((ny, nx), 2, np.int16); code[:, 140:] = 102; code[:3, :] = 101
    z = dict(code=code, curing=np.ones((ny, nx), np.float32), zone=np.zeros((ny, nx), np.int16),
             hazard=((code >= 1) & (code <= 7)).astype(np.uint8), fbfm40=np.zeros((ny, nx), np.int16))
    T0 = pd.Timestamp("2019-07-10 06:00:00")
    cxm, cym = x0 + 40 * cs, y1 - 75 * cs  # fire origin (west third)
    rows = []
    def add(ep, pts):
        t = T0 + pd.Timedelta(hours=6 * ep)
        sh = (shift_epoch or {}).get(ep, 0) * cs
        for (x, y) in pts:
            rows.append(dict(x=x + sh, y=y, t=t, ep=ep, nh=True, frp=float(rng.uniform(2, 20)) * (frp_scale or {}).get(ep, 1.0), type=0))
    add(0, [(cxm + i * cs, cym + j * cs) for i in range(-1, 2) for j in range(-1, 2)])
    add(1, [(cxm + 500 + i * cs, cym + j * cs) for i in range(0, 3) for j in range(-3, 4)])
    add(2, [(cxm + 1200 + i * cs, cym + j * cs) for i in range(0, 4) for j in range(-4, 5)])
    add(3, [(cxm + 2000 + i * cs, cym + j * cs) for i in range(0, 4) for j in range(-5, 6)] + [(cxm + 6000, cym + 300)])
    d = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
    per = shapely.Polygon([(cxm - 1500, cym - 3000), (cxm + 8000, cym - 3000), (cxm + 8000, cym + 3000), (cxm - 1500, cym + 3000)])
    if big_perimeter:
        per = shapely.box(x0 + 2 * cs, y1 - (ny - 2) * cs, x0 + (nx - 2) * cs, y1 - 2 * cs)
    tr = Transformer.from_crs(g["crs"], 4326, always_xy=True)
    per4326 = shapely.transform(per, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
    rec = dict(fire_key="SYN:1", region="CAN", year=2019, geometry=per4326, t0=T0 - pd.Timedelta(days=1), t1=pd.NaT)
    meta = dict(fire_key="SYN:1", region="CAN", year=2019, grid=g, zone_station=["X"], eligible_station=True,
                stations=[dict(sid="X", dist_km=12.0)], fuel_restored_share=0.1)
    idx = pd.date_range("2019-07-08", "2019-07-13", freq="h")
    wx = pd.DataFrame(dict(temp=25.0, rh=30.0, ws=20.0, wd=270.0, obs=1, rain=0.0, ffmc=92.0, dmc=50.0, dc=300.0, bui=70.0), index=idx)
    ctx = F.make_context("SYN:1", meta, rec, z, d, per, {"X": wx}, beta=dict(b0=-22.0, b_w=0.0, b_L=-4.0, b_m=-0.244),
                         with_texture=True, final_tol=375.0)
    return ctx


def products(ctx):
    return list(F.iter_intervals(ctx, guard=None))


def assert_cells_identical(a, b):
    assert [p.epoch for p in a] == [p.epoch for p in b]
    for pa, pb in zip(a, b):
        assert set(pa.cells) == set(pb.cells)
        for k in pa.cells:
            np.testing.assert_array_equal(pa.cells[k], pb.cells[k], err_msg=k)
        for k in pa.interval:
            assert pa.interval[k] == pb.interval[k] or (isinstance(pa.interval[k], float) and np.isnan(pa.interval[k]) and np.isnan(pb.interval[k])), k


# ── (c), (f): contract and forbidden names ──────────────────────────────────────────────────────

def test_every_builder_column_is_in_the_section_4_contract():
    assert set(F.CELL_FEATURES) <= CONTRACT, set(F.CELL_FEATURES) - CONTRACT
    assert not (set(F.CELL_FEATURES) & FORBIDDEN)
    ips = products(synthetic())
    assert len(ips) == 3
    for ip in ips:
        assert set(ip.cells) == set(F.CELL_FEATURES)
        assert set(ip.cells) <= CONTRACT and not (set(ip.cells) & FORBIDDEN)
        assert {"bi", "bj", "in_final", "code_entropy_1km", "patch_size", "edge_density_1km"} <= set(ip.control)
    assert set(B.INTERVAL_COLUMNS) == {"fire_key", "epoch", "t_start", "t_end", "gap_h", "n_candidates", "n_jumps", "region", "year",
                                       "u10_ms", "wd_deg", "m_ff_pct", "ffmc", "temp_c", "rh_pct", "act_len_km", "act_share",
                                       "fire_area_km2", "fire_age_h", "I_head_max_kwm"}


def test_candidate_set_and_units_are_sane():
    ips = products(synthetic())
    ip = ips[-1]
    c = ip.cells
    assert ip.n > 100
    assert c["d_all_km"].min() >= 1.0 and c["d_all_km"].max() <= 15.0
    assert np.all(c["d_act_km"] >= c["d_all_km"] - 1e-6)
    assert np.all(((c["bear_off_deg"] <= 45.0) == (c["sec_act"] == 1)))
    assert 0.0 <= c["bear_off_deg"].min() and c["bear_off_deg"].max() <= 180.0
    assert np.isclose(c["u10_ms"][0], 20.0 / 3.6) and np.isclose(c["wd_deg"][0], 270.0) and np.isclose(c["ffmc"][0], 92.0)
    assert np.isclose(c["temp_c"][0], 25.0) and np.isclose(c["rh_pct"][0], 30.0)
    assert np.isclose(c["m_ff_pct"][0], 147.2 * (101 - 92) / (59.5 + 92))
    assert c["gap_h"][0] == 6.0 and np.isclose(c["fire_age_h"][0], 12.0)
    assert set(np.unique(c["fuel_code"])) <= {2, 101, 102} and set(np.unique(c["fuel_group"])) <= {2, -1}
    assert np.all(c["curing_pct"] == 100.0) and c["stn_dist_km"][0] == 12.0 and np.isclose(c["fuel_restored_share"][0], 0.1)
    assert c["region"][0] == 0 and c["year"][0] == 2019 and c["sat_era"][0] == 1
    assert c["act_len_km"][0] > 0 and 0 < c["act_share"][0] <= 1 and c["fire_area_km2"][0] > 0
    assert np.nanmax(c["I_head_kwm"]) > 0 and np.nanmin(c["ros_mmin"]) >= Iv.ROS_FLOOR
    assert np.isfinite(c["spread_prev_km"]).all() and np.nanmax(c["spread_prev_km"]) > 0
    assert np.isfinite(c["d_water_km"]).all() and np.isfinite(c["d_nonfuel_km"]).all()
    assert np.all(c["b1"] >= 0) and np.isfinite(c["m8_log_frozen"]).all()
    assert c["frp_prev_mw"][0] > 0
    assert ips[0].cells["spread_prev_km"].shape == (ips[0].n,) and np.isnan(ips[0].cells["spread_prev_km"]).all()  # no previous perimeter
    assert np.all(ips[0].cells["ros_mmin"] == Iv.ROS_FLOOR)


# ── (d): nothing depends on epoch i ─────────────────────────────────────────────────────────────

def test_features_unchanged_when_epoch_i_is_perturbed():
    base = products(synthetic())
    moved = products(synthetic(shift_epoch={3: 9}, frp_scale={3: 7.0}))
    assert_cells_identical(base, moved)
    # and the control columns too (the box coordinates of the same candidate set)
    for a, b in zip(base, moved):
        np.testing.assert_array_equal(a.control["bi"], b.control["bi"]); np.testing.assert_array_equal(a.control["bj"], b.control["bj"])


def test_features_change_when_an_earlier_epoch_moves():
    base = products(synthetic())
    moved = products(synthetic(shift_epoch={2: 6}))
    ip3 = {p.epoch: p for p in moved}[3]; b3 = {p.epoch: p for p in base}[3]
    assert not np.array_equal(ip3.recomputed["d_act"], b3.recomputed["d_act"]) or ip3.n != b3.n


# ── (e): FRP from epoch i-1 only ────────────────────────────────────────────────────────────────

def test_frp_uses_epoch_i_minus_1_only():
    base = {p.epoch: p for p in products(synthetic())}
    f3 = base[3].cells["frp_prev_mw"][0]
    assert f3 > 0
    doubled = {p.epoch: p for p in products(synthetic(frp_scale={2: 2.0}))}
    assert np.isclose(doubled[3].cells["frp_prev_mw"][0], 2 * f3)
    others = {p.epoch: p for p in products(synthetic(frp_scale={0: 5.0, 1: 5.0, 3: 5.0}))}
    assert others[3].cells["frp_prev_mw"][0] == f3
    assert np.isclose(others[2].cells["frp_prev_mw"][0], 5 * base[2].cells["frp_prev_mw"][0])


# ── final perimeter: control only ───────────────────────────────────────────────────────────────

def test_final_perimeter_enters_only_the_control_column():
    a = products(synthetic()); b = products(synthetic(big_perimeter=True))
    assert_cells_identical(a, b)
    assert not np.array_equal(a[-1].control["in_final"], b[-1].control["in_final"])
    assert b[-1].control["in_final"].mean() > a[-1].control["in_final"].mean()


# ── sibling primitives equal the frozen v8 ones ────────────────────────────────────────────────

def test_fbp_at_sources_equals_tfc_intensity():
    rng = np.random.default_rng(1)
    names = rng.choice([Iv.TI[t] for t in ("C2", "C3", "M1", "M2", "D1", "O1b", "S1")], 60).astype(np.int8)
    pcs = np.where(np.isin(names, [Iv.TI["M1"], Iv.TI["M2"]]), rng.choice([25.0, 50.0, 75.0], 60), 0.0).astype(np.float32)
    ros = rng.uniform(0.5, 40, 60)
    tfc, inten = Iv.tfc_intensity(names, pcs, ros, 91.0, 80.0, 97.0)
    tfc2, inten2, cfb = F.fbp_at_sources(names, pcs, ros, 91.0, 80.0, 97.0)
    np.testing.assert_array_equal(tfc, tfc2); np.testing.assert_array_equal(inten, inten2)
    assert np.all((cfb >= 0) & (cfb <= 1)) and cfb.max() > 0


def test_interval_weather_equals_zone_weather():
    idx = pd.date_range("2019-07-08", "2019-07-13", freq="h")
    rng = np.random.default_rng(3)
    wx = pd.DataFrame(dict(temp=rng.uniform(10, 35, len(idx)), rh=rng.uniform(10, 80, len(idx)), ws=rng.uniform(0, 40, len(idx)),
                           wd=rng.uniform(0, 360, len(idx)), obs=rng.random(len(idx)) > 0.3, rain=0.0, ffmc=rng.uniform(80, 95, len(idx)),
                           dmc=50.0, dc=300.0, bui=70.0), index=idx)
    t0, t1 = pd.Timestamp("2019-07-10 06:00"), pd.Timestamp("2019-07-10 17:00")
    a, b = Iv.zone_weather(wx, t0, t1), F.interval_weather(wx, t0, t1)
    for k in a:
        assert a[k] == b[k], k
    assert np.isfinite(b["temp"]) and np.isfinite(b["rh"])


# ── (g): negative subsampling ───────────────────────────────────────────────────────────────────

def test_subsample_counts_weights_and_seed():
    y = np.zeros(5000, np.int8); y[[10, 20, 30]] = 1
    idx, w = B.subsample(y, np.random.default_rng(P.S_NEG + 4))
    assert len(idx) == 3 + 200 and len(np.unique(idx)) == 203
    assert np.all(y[idx[:3]] == 1) and np.all(y[idx[3:]] == 0)
    assert np.all(w[:3] == 1.0) and np.allclose(w[3:], 4997 / 200)
    assert np.isclose(w.sum(), 3 + 4997)  # weights restore the candidate count
    # fewer negatives than 200: all of them, weight 1
    y2 = np.zeros(120, np.int8); y2[0] = 1
    idx2, w2 = B.subsample(y2, np.random.default_rng(1))
    assert len(idx2) == 120 and np.all(w2 == 1.0)
    # empty intervals: 25 % sampled, 50 negatives each, weight n/(0.25*50)
    rng = np.random.default_rng(P.S_NEG + 0)
    draws = [B.subsample(np.zeros(3000, np.int8), rng) for _ in range(400)]
    ns = [len(i) for i, _ in draws]
    assert set(ns) <= {0, 50} and 60 <= sum(n == 50 for n in ns) <= 140
    for i, w in draws:
        if len(i):
            assert np.allclose(w, 3000 / (0.25 * 50))
    # reproducible by seed, different across fires
    a = B.subsample(y, np.random.default_rng(P.S_NEG + 4))[0]; b = B.subsample(y, np.random.default_rng(P.S_NEG + 4))[0]
    c = B.subsample(y, np.random.default_rng(P.S_NEG + 5))[0]
    assert np.array_equal(a, b) and not np.array_equal(a, c)
    assert P.S_NEG == 2026102501 and P.N_NEG_POSITIVE_INTERVAL == 200 and P.P_EMPTY_INTERVAL == 0.25 and P.N_NEG_EMPTY_INTERVAL == 50


def test_row_ids_unique_across_fires_epochs_cells():
    ids = np.concatenate([B.row_ids(fi, ep, np.arange(1000)) for fi in (0, 1, 388) for ep in (1, 2, 3000)])
    assert len(np.unique(ids)) == len(ids) and ids.dtype == np.int64


# ── (f) on the written tables, (h) on the pool ─────────────────────────────────────────────────

@pytest.mark.skipif(not (P.TABLE_DIR / "cells_fit.parquet").exists(), reason="frozen tables not built")
def test_written_cell_tables_obey_the_contract():
    pq = pytest.importorskip("pyarrow.parquet")
    for name in ("cells_eval", "cells_fit"):
        cols = set(pq.read_schema(P.TABLE_DIR / f"{name}.parquet").names)
        assert cols <= CONTRACT | IDS, cols - (CONTRACT | IDS)
        assert not (cols & FORBIDDEN)
        schema = pq.read_schema(P.TABLE_DIR / f"{name}.parquet")
        for fld in schema:
            if fld.name in CONTRACT and fld.name not in F.INT_COLUMNS:
                assert str(fld.type) == "float", (fld.name, fld.type)
    cc = set(pq.read_schema(P.TABLE_DIR / "coords_control.parquet").names)
    assert cc == {"row_id", "fire_key", "epoch", "bi", "bj"}


@pytest.mark.skipif(not POOL_PRESENT, reason="v8 data pool not present")
def test_recomputed_products_equal_the_npz_on_five_training_fires():
    L = importlib.import_module("scripts.ml_ember.loader")
    g = L.Guard()
    for k in VERIFY_FIRES:
        r = F.verify_fire(k, g)
        assert r["status"] == "ok", (k, r)
        for fld in F.NPZ_FIELDS:
            assert r["max_abs_diff"][fld] == 0.0, (k, fld, r["max_abs_diff"][fld])
        assert r["max_abs_diff"]["n_cand_vs_done"] == 0.0 and r["max_abs_diff"]["epochs_match_done"] == 0.0

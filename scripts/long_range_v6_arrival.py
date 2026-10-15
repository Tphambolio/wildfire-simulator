#!/usr/bin/env python3
"""Long-range embers v6, target A: ember transport and community arrival (spec §6.8, PREREG_LR6).

Purpose
    The owner's decision of 2026-10-13 (PROJECT_RECORD decisions log) splits ember validation in
    two. Target A asks the EOC question "when could fire (front or embers) reach this
    neighbourhood?". This script runs FireSim's own grid fire model (level-set front,
    ``firesim.spread.cellular``) over each CAL FIRE fire area from the documented ignition (or the
    observed perimeter at the start of the run), with and without long-range ember spot fires, and
    scores the arrival time at community blocks, the spot-fire distance distribution and ignitions
    ahead of the front against observations.

Models (paired ensemble members: the same wind draws for both)
    A0  FireSim without long-range embers: front + FireSim's short-range spotting
        (``enable_spotting``; Albini 1979 maximum distance, heuristic landing; ``spread/spotting.py``).
    V6  A0 + long-range ember spot fires (``firesim.spread.ember_spotting.LongRangeSpotter``:
        v5 generation and two-mode transport, per-ember establishment efficiency η fitted here).

Inputs
    - grids from ``scripts/long_range_v6_data.py`` (WorldCover water/bare/built, 3DEP terrain);
    - fire set-ups (ignition, windows, wind ranges, proxy fuel, FFMC) from the v1-v4 harnesses
      (``FIRES``/``FIRES2``/``FIRES3``/``FIRES4``), unchanged, plus the run start in UTC (below);
    - station wind series (``long_range_v3_validation.station_wind``) for the time course;
    - observations of arrival: NIST TN 2135 Appendix F fire-spread points (Camp; Maranghides et al.
      2021, doi:10.6028/NIST.TN.2135, Appendix F p.256 ff.), GOFER hourly GOES perimeters (Glass,
      CZU, North Complex; Liu et al. 2024, Earth Syst. Sci. Data 16:1395, doi:10.5194/essd-16-1395-2024
      -- citation as recorded by the v4 data agent, page not checked), FIRIS perimeters (Palisades),
      VIIRS 375 m active-fire detections (NASA FIRMS; Schroeder et al. 2014 RSE 143:85).
      DINS damage is never read here.

Outputs (outside the repo): ``<out>/<fire>/<model>_eta<η>_m<k>.npz`` (arrival minutes per grid cell,
    spot events, landed-ember grid for V6), ``<out>/scores_<tag>.json``; aggregate only.

Run (repo root; one process per fire; ~0.3-1 GB each)
    PYTHONPATH=engine/src python3 scripts/long_range_v6_arrival.py run --fire camp --model V6 --eta 1e-7 --members 4 --out DIR
    PYTHONPATH=engine/src python3 scripts/long_range_v6_arrival.py score --fires camp ... --out DIR --eta 1e-7
    PYTHONPATH=engine/src python3 scripts/long_range_v6_arrival.py fit --out DIR
    PYTHONPATH=engine/src python3 scripts/long_range_v6_arrival.py prereg

Methods and citations: see ``PREREG_LR6`` below, spec §6.8 and the v6 report.
    Storey, Price, Bradstock & Sharples (2020) Fire 3(2):10 p.7 (pooled spot distances; Zotero MS74WYUD);
    Cruz et al. (2012) For. Ecol. Manage. 284:269-285, p.269/p.281 (spot fires to 33 km; 9PGICYK7);
    Sardoy et al. (2008) Combust. Flame 154:478-488 pp.483-488 (AGFRNV4F);
    Beverly & Wotton (2007) AMS preprint eq 2.02 p.5; Qin (2025) eq 3.7 pp.59-61; Filkov et al. (2017)
    PROCI 36:3263 Table 1 p.3267 (56QTE56J). Status: pre-registered test (PREREG_LR6); illustrative.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

CALFIRE = Path.home() / "dev" / "wildfire" / "validation-data" / "calfire-dins"
V6DATA = Path.home() / "dev" / "wildfire" / "validation-data" / "embers-v6"
TRAINING = ("eaton", "palisades", "camp", "tubbs", "woolsey")
HELDOUT = ("glass", "czu", "northcomplex")
COARSEN = 2  # 90 m data grid -> 180 m run grid [H] (compute: the level set costs ~ h^-3; 90 m Woolsey runs took > 15 min each)
BLOCK_M = 540.0  # community block = 3 x 3 run cells of 180 m [H]
BLOCK_CELLS = 3
MIN_BUILDINGS = 10  # a block is a community block with >= 10 buildings [H]
TOWN_BUILT_SHARE = 0.25  # WorldCover built-up share that makes a cell "town" [H]
TOWN_ROS = 0.5  # town ROS multiplier [H]; observed within-community spread 1,535-1,780 m/h
#                 (Camp, Tubbs; Masoudvaziri et al. 2021 Table 1 p.2) vs C-4/C-7 heads of ~50-150 m/min
WATER_SHARE = 0.5
BARE_SHARE = 0.5
DT_SPOT_MIN = 10.0
WIND_REF_Q = 75.0  # station speed percentile that maps to the member speed [H]
WIND_GAIN = (0.3, 1.5)
NIST_LO_MIN = 30.0  # NIST point time as an arrival upper bound; lower bound 30 min earlier [H]
VIIRS_LOWER = True  # previous overpass as a lower bound [H]; sensitivity: False
VIIRS_R_M = 375.0  # a VIIRS detection marks a block reached if within 375 m of its centre [H]
ETA_GRID = (1e-8, 1e-7, 1e-6, 3e-6, 1e-5)
LONG_MODE = "storey"
SEED = 20261013

# Run start (UTC) = ignition for point ignitions; the window start for fires already burning
# (CZU, North Complex: the observed GOFER perimeter at that time is the starting fire).
RUNS = {
    "eaton": {"start_utc": "2025-01-08 02:11", "duration": 709.0, "start": "point"},       # 18:11 PST ignition; window to 06:00 PST
    "palisades": {"start_utc": "2025-01-07 18:30", "duration": 1170.0, "start": "point"},  # 10:30 PST
    "camp": {"start_utc": "2018-11-08 14:20", "duration": 550.0, "start": "point"},        # 06:20 PST (NIST origin)
    "tubbs": {"start_utc": "2017-10-09 04:45", "duration": 495.0, "start": "point"},       # 21:45 PDT
    "woolsey": {"start_utc": "2018-11-08 22:24", "duration": 1416.0, "start": "point"},    # 14:24 PST
    "glass": {"start_utc": "2020-09-27 10:48", "duration": 1692.0, "start": "point"},      # 03:48 PDT
    "czu": {"start_utc": "2020-08-18 22:00", "duration": 1260.0, "start": "gofer"},       # 15:00 PDT
    "northcomplex": {"start_utc": "2020-09-08 05:00", "duration": 2340.0, "start": "gofer"},  # 22:00 PDT 7 Sep
}


def fire_cfg(fire: str) -> dict:
    import long_range_v4_validation as V4

    cfg = dict(V4.FIRES4.get(fire) or V4.training_cfg(fire))
    cfg.update(RUNS[fire])
    return cfg


def ffmc_of(fire: str, cfg: dict) -> float:
    import long_range_ember_validation as L1
    import long_range_v2_validation as V2

    if cfg.get("ffmc"):
        return float(cfg["ffmc"])
    if fire == "eaton":
        return float(L1.eaton_ffmc())
    return float(V2.ffmc_from_raws(cfg))


def station_series(fire: str, cfg: dict):
    """(minutes from the run start, 10 m km/h) of the fire's wind-event station."""
    import long_range_v3_validation as V3

    src = cfg.get("stop_source") or V3.STOP_SOURCES[fire]
    t, v = V3.station_wind(fire, src)
    t0_src = datetime.strptime(src["t0"], "%Y-%m-%d %H:%M")
    # src t0 is local time; RUNS start is UTC: compare through the configured windows instead.
    # offset = (src t0 local) - (run start local); local offset from UTC per fire (PST -8, PDT -7)
    tz = -8 if fire in ("eaton", "palisades", "camp", "woolsey") else -7
    start_local = datetime.strptime(cfg["start_utc"], "%Y-%m-%d %H:%M") + timedelta(hours=tz)
    off = (t0_src - start_local).total_seconds() / 60.0
    return np.asarray(t, float) + off, np.asarray(v, float)


# ------------------------------------------------------------------------------------- grid


def load_grid(fire: str, coarsen: int = COARSEN) -> dict:
    z = np.load(V6DATA / "grids" / f"{fire}.npz")
    g = {k: float(z[k]) for k in ("lat_max", "lng_min", "cell_lat", "cell_lng", "rows", "cols", "h_m")}
    arrs = {k: z[k].astype(float) for k in ("f_water", "f_bare", "f_built", "elev_m")}
    if coarsen > 1:
        R, C = int(g["rows"]) // coarsen * coarsen, int(g["cols"]) // coarsen * coarsen
        for k, a in arrs.items():
            arrs[k] = a[:R, :C].reshape(R // coarsen, coarsen, C // coarsen, coarsen).mean(axis=(1, 3))
        g.update(rows=R // coarsen, cols=C // coarsen, cell_lat=g["cell_lat"] * coarsen,
                 cell_lng=g["cell_lng"] * coarsen, h_m=g["h_m"] * coarsen)
    g["rows"], g["cols"] = int(g["rows"]), int(g["cols"])
    from firesim.data.dem_loader import slope_aspect

    mid = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
    dx = g["cell_lng"] * 111_320.0 * math.cos(math.radians(mid))
    s, a = slope_aspect(arrs["elev_m"], g["cell_lat"] * 111_320.0, dx)
    arrs["slope"], arrs["aspect"] = s, a
    g.update(arrs)
    g["lat_min"] = g["lat_max"] - g["rows"] * g["cell_lat"]
    g["lng_max"] = g["lng_min"] + g["cols"] * g["cell_lng"]
    return g


def cell_centres(g):
    r = np.arange(g["rows"])
    c = np.arange(g["cols"])
    lat = g["lat_max"] - (r + 0.5) * g["cell_lat"]
    lng = g["lng_min"] + (c + 0.5) * g["cell_lng"]
    return lat, lng


def build_inputs(g: dict, proxy: str):
    from firesim.fbp.constants import FuelType
    from firesim.spread.huygens import FuelGrid, SpreadModifierGrid, TerrainGrid

    ft = FuelType(proxy)
    nonfuel = (g["f_water"] >= WATER_SHARE) | (g["f_bare"] >= BARE_SHARE)
    town = (~nonfuel) & (g["f_built"] >= TOWN_BUILT_SHARE)
    fuel = [[None if nonfuel[r, c] else ft for c in range(g["cols"])] for r in range(g["rows"])]
    box = (g["lat_min"], g["lat_max"], g["lng_min"], g["lng_max"], g["rows"], g["cols"])
    fg = FuelGrid(fuel, *box)
    tg = TerrainGrid([list(map(float, row)) for row in g["slope"]], [list(map(float, row)) for row in g["aspect"]], *box)
    rm = np.where(town, TOWN_ROS, 1.0)
    one = [[1.0] * g["cols"] for _ in range(g["rows"])]
    mg = SpreadModifierGrid([list(map(float, row)) for row in rm], one, one, *box)
    return fg, tg, mg, nonfuel, town


# ------------------------------------------------------------------------------- members


def member_draw(cfg: dict, m: int) -> dict:
    rng = np.random.default_rng([SEED, m])
    u = float(rng.uniform(*cfg["wind_speed"]))
    d = float(rng.uniform(*cfg["wind_from"])) % 360.0
    return {"u_ms": u, "from_deg": d}


def schedule(fire, cfg, draw, ffmc, fmc):
    """Hourly (start minute, SpreadConditions): member speed × station gain [H]."""
    from firesim.spread.huygens import SpreadConditions

    t, v = station_series(fire, cfg)
    dur = cfg["duration"]
    inwin = (t >= 0) & (t <= dur)
    ref = float(np.percentile(v[inwin], WIND_REF_Q)) if inwin.sum() >= 3 else float(np.median(v))
    out = []
    for h0 in np.arange(0.0, dur, 60.0):
        k = (t >= h0 - 30) & (t < h0 + 30)
        gain = float(np.clip(np.mean(v[k]) / ref, *WIND_GAIN)) if k.any() and ref > 0 else 1.0
        out.append((float(h0), SpreadConditions(wind_speed=draw["u_ms"] * 3.6 * gain, wind_direction=draw["from_deg"],
                                                ffmc=ffmc, dmc=cfg["dmc"], dc=cfg["dc"], fmc=fmc)))
    return out


def gofer_start(fire, cfg, g):
    """Cells inside the latest GOFER perimeter at or before the run start: (lat, lng) list."""
    import shapely

    feats = json.load(open(CALFIRE / cfg["timed_perimeters"]))["features"]
    t0 = cfg["start_utc"]
    best = None
    for f in feats:
        if f["properties"]["tUTC"][:16] <= t0:
            if best is None or f["properties"]["tUTC"] > best["properties"]["tUTC"]:
                best = f
    geom = shapely.geometry.shape(best["geometry"])
    lat, lng = cell_centres(g)
    LA, LN = np.meshgrid(lat, lng, indexing="ij")
    inside = shapely.contains_xy(geom, LN.ravel(), LA.ravel()).reshape(LA.shape)
    rr, cc = np.nonzero(inside)
    return [(float(lat[r]), float(lng[c])) for r, c in zip(rr, cc)], best["properties"]["tUTC"]


def run_member(fire: str, model: str, eta: float, m: int, g=None, inputs=None, cfg=None, ffmc=None):
    from firesim.fbp.calculator import calculate_foliar_moisture
    from firesim.spread.cellular import run_cellular_simulation
    from firesim.spread.ember_spotting import LongRangeSpotter

    cfg = cfg or fire_cfg(fire)
    g = g or load_grid(fire)
    fg, tg, mg, nonfuel, town = inputs or build_inputs(g, cfg["proxy"])
    ffmc = ffmc if ffmc is not None else ffmc_of(fire, cfg)
    fmc = calculate_foliar_moisture(cfg["lat"], cfg["origin"][1], cfg["elev"], cfg["doy"])
    draw = member_draw(cfg, m)
    sched = schedule(fire, cfg, draw, ffmc, fmc)
    run_cfg = {"ignition_lat": cfg["origin"][0], "ignition_lng": cfg["origin"][1], "duration_hours": cfg["duration"] / 60.0}
    kw = {}
    if cfg["start"] == "gofer":
        kw["initial_burned"], _ = gofer_start(fire, cfg, g)
    hook = None
    if model == "V6":
        def wind_at(tm, _s=sched):
            k = max(0, min(len(_s) - 1, int(tm // 60.0)))
            return _s[k][1].wind_speed, _s[k][1].wind_direction

        hook = LongRangeSpotter(g["h_m"], wind_at, ffmc, eta=eta, long=LONG_MODE, seed=int(SEED * 10 + m))
    t = time.time()
    frames = run_cellular_simulation(run_cfg, fg, sched[0][1], spread_modifier_grid=mg, terrain_grid=tg,
                                     dt_minutes=DT_SPOT_MIN, snapshot_interval_minutes=cfg["duration"],
                                     enable_spotting=True, compute_perimeter=False, weather_schedule=sched,
                                     seed=int(SEED * 100 + m), spot_hook=hook, spot_sample_spacing_m=150.0, **kw)
    arr = frames[-1].arrival.astype(np.float32)
    lat, lng = cell_centres(g)
    alb = []
    for fr in frames:
        for s in fr.spot_fires or []:
            r = int((g["lat_max"] - s.lat) / g["cell_lat"])
            c = int((s.lng - g["lng_min"]) / g["cell_lng"])
            if 0 <= r < g["rows"] and 0 <= c < g["cols"]:
                alb.append((r, c))
    alb_ev = spot_distances(arr, alb, g["h_m"])
    out = {"arrival": arr, "albini_spots": np.array(alb_ev, dtype=np.float32).reshape(-1, 4),
           "u_ms": draw["u_ms"], "from_deg": draw["from_deg"], "ffmc": ffmc, "seconds": time.time() - t}
    if hook is not None:
        ev = np.array([(e.t_min, e.row, e.col, e.dist_to_fire_m) for e in hook.events], dtype=np.float32).reshape(-1, 4)
        out["lr_spots"] = ev
        out["emitted"] = hook.emitted
        out["landed"] = hook.landed.astype(np.float32) if getattr(hook, "landed", None) is not None else np.zeros(1)
    return out


def spot_distances(arr, cells, h):
    """(t, r, c, distance to cells burned before t) for spot cells, from the final arrival grid
    (one distance transform per distinct ignition time)."""
    from scipy import ndimage

    out = []
    by_t = {}
    for r, c in cells:
        t = float(arr[r, c])
        if np.isfinite(t):
            by_t.setdefault(round(t, 3), []).append((r, c))
    for t, rc in sorted(by_t.items()):
        burned = arr < t - 1e-3
        if not burned.any():
            continue
        d = ndimage.distance_transform_edt(~burned) * h
        out += [(t, r, c, float(d[r, c])) for r, c in rc]
    return out


def cmd_run(args):
    cfg = fire_cfg(args.fire)
    g = load_grid(args.fire)
    inputs = build_inputs(g, cfg["proxy"])
    ffmc = ffmc_of(args.fire, cfg)
    od = args.out / args.fire
    od.mkdir(parents=True, exist_ok=True)
    for model in args.model:
        etas = args.eta if model == "V6" else [0.0]
        for eta in etas:
            for m in range(args.first, args.first + args.members):
                p = od / f"{model}_eta{eta:g}_m{m}.npz"
                if p.exists() and not args.redo:
                    continue
                r = run_member(args.fire, model, eta, m, g, inputs, cfg, ffmc)
                np.savez_compressed(p, **r)
                n_lr = len(r.get("lr_spots", []))
                print(f"{args.fire} {model} eta {eta:g} m{m}: burned {np.isfinite(r['arrival']).sum()} cells, "
                      f"albini {len(r['albini_spots'])} lr {n_lr} spots | u {r['u_ms']:.1f} m/s from {r['from_deg']:.0f} | "
                      f"{r['seconds']:.0f} s", flush=True)


# --------------------------------------------------------------------------- observations


def _utc(s):
    return datetime.strptime(s[:16].replace("T", " "), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def timed_perimeters(fire, cfg):
    """[(minutes from run start, shapely geometry lng/lat)], cumulative, ascending."""
    import shapely

    t0 = _utc(cfg["start_utc"])
    out = []
    if cfg.get("timed_perimeters"):
        for f in json.load(open(CALFIRE / cfg["timed_perimeters"]))["features"]:
            out.append(((_utc(f["properties"]["tUTC"]) - t0).total_seconds() / 60.0, shapely.geometry.shape(f["geometry"])))
    if fire == "palisades":  # FIRIS perimeters: time added to the WatchDuty feed (an upper bound) [H]
        for f in json.load(open(CALFIRE / "refs/palisades/FIRIS_WatchDuty_Palisades_perimeters_20250107-08.geojson"))["features"]:
            tu = datetime.fromtimestamp(f["properties"]["date_added"] / 1000.0, tz=timezone.utc)
            out.append(((tu - t0).total_seconds() / 60.0, shapely.geometry.shape(f["geometry"])))
    out.sort(key=lambda x: x[0])
    return out


def viirs(fire, cfg):
    p = CALFIRE / "derived" / "v4" / f"{fire}_viirs.csv"
    t0 = _utc(cfg["start_utc"])
    rows = [r for r in csv.DictReader(open(p)) if str(r.get("confidence", "n")).lower()[:1] != "l"]
    lat = np.array([float(r["lat"]) for r in rows])
    lon = np.array([float(r["lon"]) for r in rows])
    t = np.array([(_utc(r["acq_datetime_utc"]) - t0).total_seconds() / 60.0 for r in rows])
    return lat, lon, t


def nist_points(cfg):
    """Camp: NIST TN 2135 Appendix F fire-presence points (types V, O, S), minutes from 06:20 PST."""
    feats = json.load(open(CALFIRE / "derived" / "v4" / "camp_nist_fronts.geojson"))["features"]
    lat = np.array([f["properties"]["lat"] for f in feats])
    lon = np.array([f["properties"]["lon"] for f in feats])
    t = np.array([float(f["properties"]["minutes_start"]) for f in feats])
    typ = np.array([f["properties"]["type"] for f in feats])
    return lat, lon, t, typ


def building_points(fire, cfg, g):
    """Building centroids (lng, lat) in the grid box: Microsoft US v2 footprints (+ the DINS-point units
    for Camp, whose footprints are post-fire). Counts only; never written out per building."""
    xs, ys = [], []
    fp = CALFIRE / "derived" / f"ms_usv2_{fire}.geojsonl"
    with open(fp) as f:
        for line in f:
            geom = json.loads(line)["geometry"]
            ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
            a = np.asarray(ring)
            xs.append(a[:, 0].mean())
            ys.append(a[:, 1].mean())
    if fire == "camp":
        import geopandas as gpd

        u = gpd.read_file(CALFIRE / "derived" / "camp_units.gpkg").to_crs(4326)
        c = u.geometry.representative_point()
        xs += list(c.x)
        ys += list(c.y)
    return np.array(xs), np.array(ys)


def blocks(g):
    B = BLOCK_CELLS
    nbr, nbc = g["rows"] // B, g["cols"] // B
    lat = g["lat_max"] - (np.arange(nbr) + 0.5) * B * g["cell_lat"]
    lng = g["lng_min"] + (np.arange(nbc) + 0.5) * B * g["cell_lng"]
    return nbr, nbc, lat, lng


def observed_blocks(fire, cfg, g):
    """Per community block: building count, observed arrival interval [t_lo, t_hi] (minutes from the
    run start; inf = not observed reached), the source of t_hi, and whether the block lies outside the
    final FRAP perimeter (never burned)."""
    import shapely

    nbr, nbc, blat, blng = blocks(g)
    B = BLOCK_CELLS
    bx, by = building_points(fire, cfg, g)
    r = np.floor((g["lat_max"] - by) / (B * g["cell_lat"])).astype(int)
    c = np.floor((bx - g["lng_min"]) / (B * g["cell_lng"])).astype(int)
    ok = (r >= 0) & (r < nbr) & (c >= 0) & (c < nbc)
    nb = np.zeros((nbr, nbc), int)
    np.add.at(nb, (r[ok], c[ok]), 1)
    LA, LN = np.meshgrid(blat, blng, indexing="ij")
    t_hi = np.full((nbr, nbc), np.inf)
    t_lo = np.zeros((nbr, nbc))
    src = np.zeros((nbr, nbc), "U6")
    mlat = 111_320.0
    mlng = 111_320.0 * math.cos(math.radians(float(np.mean(blat))))
    # Upper bounds (the block is burning or burned by then): VIIRS detection within 375 m, the first
    # timed perimeter containing the block centre, the first NIST TN 2135 point in the block (Camp).
    vla, vlo, vt = viirs(fire, cfg)
    from scipy.spatial import cKDTree

    if len(vt):
        tree = cKDTree(np.column_stack([vlo * mlng, vla * mlat]))
        nn = tree.query_ball_point(np.column_stack([LN.ravel() * mlng, LA.ravel() * mlat]), VIIRS_R_M)
        tv = np.array([vt[i].min() if len(i) else np.inf for i in nn]).reshape(LA.shape)
        tv = np.where(tv >= 0, tv, 0.0)
        upd = tv < t_hi
        t_hi = np.where(upd, tv, t_hi)
        src = np.where(upd, "viirs", src)
    per = timed_perimeters(fire, cfg)
    ins_t = []
    inside_prev = np.zeros(LA.shape, bool)
    for tm, geom in per:
        ins = shapely.contains_xy(geom, LN.ravel(), LA.ravel()).reshape(LA.shape)
        ins_t.append((max(tm, 0.0), ins))
        new = ins & ~inside_prev
        upd = new & (max(tm, 0.0) < t_hi)
        t_hi = np.where(upd, max(tm, 0.0), t_hi)
        src = np.where(upd, "perim", src)
        inside_prev |= ins
    nist_lo = np.zeros(LA.shape)
    if fire == "camp":
        nla, nlo, nt, typ = nist_points(cfg)
        rr = np.floor((g["lat_max"] - nla) / (B * g["cell_lat"])).astype(int)
        cc = np.floor((nlo - g["lng_min"]) / (B * g["cell_lng"])).astype(int)
        for a, b, t in zip(rr, cc, nt):
            if 0 <= a < nbr and 0 <= b < nbc and t < t_hi[a, b]:
                t_hi[a, b] = t
                src[a, b] = "nist"
                nist_lo[a, b] = max(t - NIST_LO_MIN, 0.0)
    # Lower bounds (the block was not yet reached), only from observations strictly before t_hi:
    # (a) a timed perimeter not containing the block; (b) the last VIIRS overpass (any detection in
    # the box) before the block's first detection, without a detection near the block [H: a burning
    # community block is detectable at the next overpass; structure fires burn for hours];
    # (c) NIST: 30 min before the point time [H].
    for tm, ins in ins_t:
        t_lo = np.where(~ins & (tm < t_hi) & (tm > t_lo), tm, t_lo)
    if len(vt) and VIIRS_LOWER:
        ov = np.unique(np.round(vt[(vt >= 0)], 0))
        k = np.searchsorted(ov, t_hi, side="left") - 1
        prev = np.where(k >= 0, ov[np.clip(k, 0, None)], 0.0)
        prev = np.where(np.isfinite(t_hi) & (prev < t_hi), prev, 0.0)
        t_lo = np.maximum(t_lo, prev)
    t_lo = np.where(src == "nist", np.maximum(t_lo, nist_lo), t_lo)
    t_lo = np.minimum(t_lo, t_hi)
    # outside the final perimeter (+ 1 block) = never burned
    fin = json.load(open(CALFIRE / cfg["perimeter"]))
    fp = shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in fin["features"]])
    fpb = shapely.buffer(fp, B * g["cell_lat"])
    outside = ~shapely.contains_xy(fpb, LN.ravel(), LA.ravel()).reshape(LA.shape)
    return {"n_bld": nb, "t_lo": t_lo, "t_hi": t_hi, "src": src, "outside": outside, "lat": blat, "lng": blng}


def observed_ahead(fire, cfg, g):
    """Observed ignitions ahead of the fire: Camp NIST type 'S' (spot fire) points; other fires:
    VIIRS detections > 1 km from every earlier detection and outside every earlier timed perimeter.
    Returns (lat, lon, t) within the run."""
    import shapely

    if fire == "camp":
        la, lo, t, typ = nist_points(cfg)
        k = (typ == "S") & (t <= cfg["duration"])
        return la[k], lo[k], t[k]
    la, lo, t = viirs(fire, cfg)
    order = np.argsort(t)
    la, lo, t = la[order], lo[order], t[order]
    per = timed_perimeters(fire, cfg)
    mlng = 111_320.0 * math.cos(math.radians(float(np.mean(la)) if len(la) else 35.0))
    keep = []
    for i in range(len(t)):
        if t[i] < 0 or t[i] > cfg["duration"]:
            continue
        prev = t < t[i] - 1.0
        if not prev.any():
            continue
        d = np.hypot((lo[prev] - lo[i]) * mlng, (la[prev] - la[i]) * 111_320.0).min()
        if d <= 1000.0:
            continue
        inside = any(tm < t[i] and shapely.contains_xy(geom, lo[i], la[i]) for tm, geom in per)
        if not inside:
            keep.append(i)
    keep = np.array(keep, int)
    return la[keep], lo[keep], t[keep]


# -------------------------------------------------------------------------------- scoring


def block_arrival(arr, g):
    """Earliest arrival per block (minutes; inf = not reached)."""
    B = BLOCK_CELLS
    nbr, nbc = g["rows"] // B, g["cols"] // B
    a = arr[: nbr * B, : nbc * B].reshape(nbr, B, nbc, B)
    return a.min(axis=(1, 3))


def interval_err(a, lo, hi):
    """Distance (min) of a model time from the observed interval [lo, hi]."""
    return np.where(a < lo, lo - a, np.where(a > hi, a - hi, 0.0))


def load_members(od: Path, model: str, eta: float, members=None):
    files = sorted(od.glob(f"{model}_eta{eta:g}_m*.npz"), key=lambda p: int(p.stem.split("_m")[-1]))
    if members:
        files = [p for p in files if int(p.stem.split("_m")[-1]) < members]
    return [dict(np.load(p)) for p in files]


def score_fire(fire, out: Path, eta: float, members=None, keep_maps=False):
    cfg = fire_cfg(fire)
    g = load_grid(fire)
    ob = observed_blocks(fire, cfg, g)
    dur = cfg["duration"]
    comm = ob["n_bld"] >= MIN_BUILDINGS
    scored = comm & np.isfinite(ob["t_hi"]) & (ob["t_hi"] <= dur)
    res = {"fire": fire, "duration_min": dur, "blocks_community": int(comm.sum()), "blocks_scored": int(scored.sum()),
           "t_hi_source": {k: int(np.sum(scored & (ob["src"] == k))) for k in ("viirs", "perim", "nist")},
           "two_sided_share": float(np.mean(ob["t_lo"][scored] > 0)) if scored.any() else None, "models": {}}
    taus = np.arange(60.0, dur + 1e-9, 60.0)
    ahead = observed_ahead(fire, cfg, g)
    res["observed_ahead_n"] = int(len(ahead[2]))
    maps = {}
    for model, e in (("A0", 0.0), ("V6", eta)):
        mem = load_members(out / fire, model, e, members)
        if not mem:
            continue
        BA = np.array([block_arrival(m["arrival"], g) for m in mem])  # members x blocks
        BAc = np.where(np.isfinite(BA), BA, dur)  # not reached by the end: the end (lower bound)
        med = np.median(BAc, axis=0)
        p10 = np.percentile(BAc, 10, axis=0)
        err = interval_err(med, ob["t_lo"], ob["t_hi"])[scored] / 60.0
        # reach-by-tau (known statuses only)
        P, O = [], []
        se_sum = np.zeros(comm.shape)
        se_n = np.zeros(comm.shape)
        for tau in taus:
            pr = np.mean(BA <= tau, axis=0)
            yes = scored & (ob["t_hi"] <= tau)
            no = comm & ((ob["t_lo"] >= tau) | ob["outside"])
            k = yes | no
            P.append(pr[k])
            O.append(yes[k].astype(float))
            se_sum[k] += (pr[k] - yes[k]) ** 2
            se_n[k] += 1
        P = np.concatenate(P) if P else np.array([])
        O = np.concatenate(O) if O else np.array([])
        bri_blocks = se_n > 0
        bri_b = np.where(bri_blocks, se_sum / np.maximum(se_n, 1), np.nan)
        # ahead ignitions: hit = model fire within one block (540 m) of the point by its time + 30 min (member share)
        hits = []
        for la, lo, t in zip(*ahead):
            r = int((g["lat_max"] - la) / g["cell_lat"])
            c = int((lo - g["lng_min"]) / g["cell_lng"])
            if not (0 <= r < g["rows"] and 0 <= c < g["cols"]):
                continue
            r0, r1, c0, c1 = max(0, r - BLOCK_CELLS), r + BLOCK_CELLS + 1, max(0, c - BLOCK_CELLS), c + BLOCK_CELLS + 1
            hits.append(np.mean([np.nanmin(m["arrival"][r0:r1, c0:c1]) <= t + 30.0 for m in mem]))
        sp = [m.get("lr_spots") for m in mem if m.get("lr_spots") is not None and len(m.get("lr_spots"))]
        lr = np.concatenate(sp) if sp else np.zeros((0, 4))
        al = np.concatenate([m["albini_spots"] for m in mem if len(m["albini_spots"])]) if any(
            len(m["albini_spots"]) for m in mem) else np.zeros((0, 4))
        # false alarms: spot fires outside the final perimeter (+ 1 block)
        def outside_share(ev):
            if not len(ev):
                return None
            br = (ev[:, 1] // BLOCK_CELLS).astype(int)
            bc = (ev[:, 2] // BLOCK_CELLS).astype(int)
            okb = (br < ob["outside"].shape[0]) & (bc < ob["outside"].shape[1])
            return float(np.mean(ob["outside"][br[okb], bc[okb]]))
        burned_cells = np.mean([np.isfinite(m["arrival"]).sum() for m in mem]) * (g["h_m"] ** 2) / 1e4
        res["models"][model] = {
            "eta": e, "members": len(mem), "mae_h": float(err.mean()) if len(err) else None,
            "median_err_h": float(np.median(err)) if len(err) else None,
            "share_within_1h": float(np.mean(err <= 1.0)) if len(err) else None,
            "late_share_p10": float(np.mean(p10[scored] > ob["t_hi"][scored])) if scored.any() else None,
            "brier_reach": float(np.mean((P - O) ** 2)) if len(P) else None, "n_reach_pairs": int(len(P)),
            "ahead_hit_mean": float(np.mean(hits)) if hits else None, "ahead_n": len(hits),
            "lr_spots_per_member": float(len(lr) / len(mem)), "albini_spots_per_member": float(len(al) / len(mem)),
            "lr_outside_share": outside_share(lr), "albini_outside_share": outside_share(al),
            "burned_ha_mean": float(burned_cells),
            "blocks_reached_mean": float(np.mean(np.isfinite(BA)[:, comm].sum(axis=1))),
        }
        maps[model] = {"err_b": interval_err(med, ob["t_lo"], ob["t_hi"]), "med": med, "P": P, "O": O,
                       "bri_b": bri_b, "bri_mask": bri_blocks, "p10": p10,
                       "lr": lr, "al": al, "BA": BA}
    if keep_maps:
        return res, maps, ob, scored, g
    return res


def cluster_ids(ob, scored, km=2.0):
    """Bootstrap clusters: 2 km super-blocks (spatial dependence) [H]."""
    nbr, nbc = scored.shape
    k = max(1, int(round(km * 1000.0 / BLOCK_M)))
    rr, cc = np.nonzero(scored)
    return (rr // k) * 100000 + (cc // k)


def boot_mae_diff(errs_a, errs_b, clusters, n=2000, seed=SEED):
    """Mean(errs_b - errs_a) with a cluster bootstrap (percentile 95 % CI)."""
    rng = np.random.default_rng(seed)
    d = np.asarray(errs_b) - np.asarray(errs_a)
    u, inv = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inv, weights=d)
    cnt = np.bincount(inv)
    stats = []
    for _ in range(n):
        s = rng.integers(0, len(u), len(u))
        stats.append(sums[s].sum() / cnt[s].sum())
    return {"diff": float(d.mean()), "p2.5": float(np.percentile(stats, 2.5)), "p97.5": float(np.percentile(stats, 97.5)),
            "clusters": int(len(u)), "n": int(len(d))}


def cmd_score(args):
    out = {"_about": {"purpose": "PREREG_LR6 target A scores (community-block arrival)", "units": "hours, shares",
                      "status": args.tag, "prereg_sha256": prereg_hash()}, "fires": {}}
    pooled = {"A0": [], "V6": [], "cl": [], "bA0": [], "bV6": [], "bcl": []}
    for i, fire in enumerate(args.fires):
        res, maps, ob, scored, g = score_fire(fire, args.out, args.eta, args.members, keep_maps=True)
        out["fires"][fire] = res
        if "A0" in maps and "V6" in maps and scored.any():
            ea = maps["A0"]["err_b"][scored] / 60.0
            ev = maps["V6"]["err_b"][scored] / 60.0
            cl = cluster_ids(ob, scored) + i * 10**9
            res["mae_diff_V6_minus_A0"] = boot_mae_diff(ea, ev, cl)
            pooled["A0"].append(ea)
            pooled["V6"].append(ev)
            pooled["cl"].append(cl)
            bm = maps["A0"]["bri_mask"] & maps["V6"]["bri_mask"]
            ba, bv = maps["A0"]["bri_b"][bm], maps["V6"]["bri_b"][bm]
            bcl = cluster_ids(ob, bm) + i * 10**9
            res["brier_diff_V6_minus_A0"] = boot_mae_diff(ba, bv, bcl)
            pooled["bA0"].append(ba)
            pooled["bV6"].append(bv)
            pooled["bcl"].append(bcl)
        print(json.dumps({fire: {k: res["models"][k]["mae_h"] for k in res["models"]}}), flush=True)
    if pooled["A0"]:
        out["pooled"] = boot_mae_diff(np.concatenate(pooled["A0"]), np.concatenate(pooled["V6"]),
                                      np.concatenate(pooled["cl"]))
        out["pooled"]["mae_A0_h"] = float(np.concatenate(pooled["A0"]).mean())
        out["pooled"]["mae_V6_h"] = float(np.concatenate(pooled["V6"]).mean())
        out["pooled_brier"] = boot_mae_diff(np.concatenate(pooled["bA0"]), np.concatenate(pooled["bV6"]),
                                            np.concatenate(pooled["bcl"]))
        out["pooled_brier"]["brier_A0"] = float(np.concatenate(pooled["bA0"]).mean())
        out["pooled_brier"]["brier_V6"] = float(np.concatenate(pooled["bV6"]).mean())
        d, b = out["pooled"], out["pooled_brier"]
        out["decision"] = {"A1_mae_better": d["p97.5"] < 0, "A2_brier_not_worse": b["p2.5"] <= 0}
        out["decision"]["embers_add_skill_A"] = out["decision"]["A1_mae_better"] and out["decision"]["A2_brier_not_worse"]
    out["spot_tail"] = spot_tail(args.fires, args.out, args.eta, args.members)
    (args.out / f"scores_{args.tag}.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: out.get(k) for k in ("pooled", "pooled_brier", "decision")}, indent=1),
          json.dumps(out["spot_tail"], indent=1))


def spot_tail(fires, out, eta, members=None):
    """Pooled spot-fire distances (to the fire at ignition): exceedance shares and the
    tail ratio P(>3 km)/P(>1 km) (Storey et al. 2020 p.7: 0.02 / 0.10 = 0.20)."""
    res = {}
    for model, key, e in (("V6", "lr_spots", eta), ("A0", "albini_spots", 0.0), ("V6_all", None, eta)):
        d = []
        per_fire_max = []
        for fire in fires:
            mem = load_members(out / fire, "V6" if model.startswith("V6") else "A0", e, members)
            for m in mem:
                parts = [m.get("lr_spots"), m.get("albini_spots")] if key is None else [m.get(key)]
                dd = np.concatenate([p[:, 3] for p in parts if p is not None and len(p)]) if any(
                    p is not None and len(p) for p in parts) else np.zeros(0)
                d.append(dd)
                if len(dd):
                    per_fire_max.append(float(dd.max()))
        d = np.concatenate(d) if d else np.zeros(0)
        if not len(d):
            res[model] = {"n": 0}
            continue
        p1, p3 = float(np.mean(d > 1000)), float(np.mean(d > 3000))
        res[model] = {"n": int(len(d)), "share_gt_1km": p1, "share_gt_3km": p3,
                      "tail_ratio_3_over_1": p3 / p1 if p1 > 0 else None,
                      "median_m": float(np.median(d)), "max_m": float(d.max()),
                      "member_max_median_m": float(np.median(per_fire_max)) if per_fire_max else None}
    res["storey_2020"] = {"share_gt_1km": 0.10, "share_gt_3km": 0.02, "tail_ratio_3_over_1": 0.20, "median_m": 100.0,
                          "per_fire_max_median_m": 640.0, "max_m": 13900.0, "source": "Storey et al. 2020 Fire 3:10 p.7"}
    return res


def cmd_fit(args):
    """LOFO over training fires. Objective (fixed 2026-10-13 after the first training scores showed
    MAE alone favouring ever more spot fires on fires with one-sided observations): per fire
    J = (MAE_V6 / MAE_A0 + Brier_V6 / Brier_A0) / 2, i.e. both PREREG_LR6 conditions with equal
    weight; eta minimises the mean J over the other fires (CV) and over all five (final)."""
    table = {}
    for fire in TRAINING:
        table[fire] = {}
        for eta in args.etas:
            r = score_fire(fire, args.out, eta, args.members)
            v, a = r["models"].get("V6", {}), r["models"].get("A0", {})
            row = {"V6": v.get("mae_h"), "A0": a.get("mae_h"), "brier_V6": v.get("brier_reach"),
                   "brier_A0": a.get("brier_reach"), "lr_spots": v.get("lr_spots_per_member"),
                   "members_V6": v.get("members"), "blocks": r["blocks_scored"],
                   "two_sided_share": r["two_sided_share"], "lr_outside_share": v.get("lr_outside_share"),
                   "late_share_p10_V6": v.get("late_share_p10"), "late_share_p10_A0": a.get("late_share_p10")}
            ok = all(row[k] for k in ("V6", "A0", "brier_V6", "brier_A0"))
            row["J"] = 0.5 * (row["V6"] / row["A0"] + row["brier_V6"] / row["brier_A0"]) if ok else None
            table[fire][f"{eta:g}"] = row
            print(fire, eta, {k: (round(x, 3) if isinstance(x, float) else x) for k, x in row.items()}, flush=True)
    keys = [f"{e:g}" for e in args.etas]

    def J(f, k):
        v = table[f][k]["J"]
        return v if v is not None else np.nan

    lofo = {}
    for f in TRAINING:
        others = [x for x in TRAINING if x != f]
        j = int(np.nanargmin([np.nanmean([J(o, k) for o in others]) for k in keys]))
        lofo[f] = {"eta": keys[j], "J": J(f, keys[j]), **{k: table[f][keys[j]][k] for k in ("V6", "A0", "brier_V6", "brier_A0")}}
    mean_j = {k: float(np.nanmean([J(o, k) for o in TRAINING])) for k in keys}
    j = int(np.nanargmin(list(mean_j.values())))
    out = {"_about": {"purpose": "v6 target A training fit (LOFO over training fires)",
                      "units": "MAE hours; Brier; J dimensionless (1 = no change vs A0, < 1 better)",
                      "objective": cmd_fit.__doc__},
           "table": table, "mean_J": mean_j, "lofo": lofo, "final_eta": float(keys[j])}
    (args.out / "fit_v6_arrival.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"mean_J": mean_j, "lofo": lofo, "final_eta": keys[j]}, indent=1, default=float))


# ------------------------------------------------------------------------------- PREREG

PREREG_LR6: dict = {}  # filled in a pushed docs commit before any held-out score (scripts/long_range_v6_prereg.py)


def prereg_hash() -> str:
    try:
        import long_range_v6_prereg as P

        blob = P.PREREG_LR6
    except Exception:
        blob = PREREG_LR6
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=float).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run")
    a.add_argument("--fire", required=True)
    a.add_argument("--model", nargs="+", default=["A0", "V6"])
    a.add_argument("--eta", nargs="+", type=float, default=[1e-7])
    a.add_argument("--members", type=int, default=4)
    a.add_argument("--first", type=int, default=0)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--redo", action="store_true")
    b = sub.add_parser("score")
    b.add_argument("--fires", nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--eta", type=float, required=True)
    b.add_argument("--members", type=int, default=None)
    b.add_argument("--tag", default="exploratory")
    c = sub.add_parser("fit")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--etas", nargs="+", type=float, default=list(ETA_GRID))
    c.add_argument("--members", type=int, default=None)
    args = ap.parse_args()
    {"run": cmd_run, "score": cmd_score, "fit": cmd_fit}[args.cmd](args)


if __name__ == "__main__":
    main()

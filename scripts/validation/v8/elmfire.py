"""C-ELMFIRE comparator runner (PREREG_LR8 sections 5, 8a.4-5; never decides).

Black box: image ``elmfire-bench:cbf924a``, User Guide keywords only, no ELMFIRE source read. One 90 m run per
valid interval of each fire of the seeded ELMFIRE subset (``default_rng(2026101805)`` permutation of the drawn
eligible decision fires, first 150). Per interval:
- ``phi`` = -1 inside the start perimeter (the same VIIRS start perimeter as M8); held edge segments (edge cells
  that are not active) and a one-cell ring outside them are made non-burnable for the interval, so ELMFIRE
  sees the same active edge as M8 and B1;
- fuels: US LANDFIRE FBFM40, CC, CH, CBH, CBD of the fire's vintage at 90 m (Rothermel); Canada the CIFFC
  national FBP codes of the FireSim pre-fire grid with ``SURFACE_SPREAD_MODEL = 'CFFDRS'``, ``START_DMC``,
  ``START_DC`` and a daily noon weather file from the zone station;
- terrain: US LANDFIRE LF2020 elevation, Canada NRCan MRDEM-30 (OGL-C), slope and aspect from the DEM;
- weather: hourly bands from the zone stations (10 m wind in km/h, ``WS_AT_10M``, ``WS_IN_KPH``), dead fuel
  moisture M1 = FF-scale moisture of the hourly FFMC, M10 = M1 + 1, M100 = M1 + 2 [H]; live 30 / 60 % [H];
- spotting pinned (comparator README): PER-MW, EMPIRICAL, EULERIAN, PHYSICAL + DIFF_WILDLAND_IGNITION, crown
  spotting 30 %; ``DUMP_EMBER_FLUX``;
- score = cumulative ember flux summed over the 90 m cells whose centres fall in each 375 m candidate cell.
Guard: ``docker run --memory 5g``; peak container memory sampled every 3 s and logged; a run log containing the
particle-mode crash string, a non-zero exit, or missing output is a run failure (ELMFIRE comparison only).
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from scipy import ndimage

from . import common as C
from . import inputs as I
from .intervals import epochs, detections, DIL, CLOSE, ACTIVE_R

IMAGE = "elmfire-bench:cbf924a"
CELL = 90.0
SEED = 2026101805
N_SUBSET = 150
RUNROOT = C.DATA / "elmfire_runs"
CRASH = "ERFINV_LOCAL"
MRDEM = "/vsicurl/https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif"

DECK = """&INPUTS
FUELS_AND_TOPOGRAPHY_DIRECTORY = './inputs'
ASP_FILENAME = 'asp'
CC_FILENAME = 'cc'
CH_FILENAME = 'ch'
{canopy}DEM_FILENAME = 'dem'
FBFM_FILENAME = 'fbfm'
SLP_FILENAME = 'slp'
ADJ_FILENAME = 'adj'
PHI_FILENAME = 'phi'
DT_METEOROLOGY = 3600.0
WEATHER_DIRECTORY = './inputs'
WS_FILENAME = 'ws'
WD_FILENAME = 'wd'
M1_FILENAME = 'm1'
M10_FILENAME = 'm10'
M100_FILENAME = 'm100'
WS_AT_10M = .TRUE.
WS_IN_KPH = .TRUE.
LH_MOISTURE_CONTENT = 30.0
LW_MOISTURE_CONTENT = 60.0
{cffdrs}/

&OUTPUTS
OUTPUTS_DIRECTORY = './outputs'
DTDUMP = {tstop}
DUMP_TIME_OF_ARRIVAL = .TRUE.
DUMP_SPOTTING_OUTPUTS = .FALSE.
DUMP_EMBER_FLUX = .TRUE.
DUMP_EMBER_IGNITION = .TRUE.
CONVERT_TO_GEOTIFF = .FALSE.
/

&TIME_CONTROL
SIMULATION_DT = 30.0
SIMULATION_TSTOP = {tstop}
CURRENT_YEAR = {year}
HOUR_OF_YEAR = {hoy}
/

&SIMULATOR
WX_BILINEAR_INTERPOLATION = .TRUE.
/

&SPOTTING
ENABLE_SPOTTING = .TRUE.
USE_SUPERSEDED_SPOTTING = .FALSE.
GENERATION_MODEL = 'PER-MW'
SPOTTING_DISTANCE_MODEL = 'EMPIRICAL'
ACCUMULATION_MODEL = 'EULERIAN'
IGNITION_MODEL = 'PHYSICAL'
DIFF_WILDLAND_IGNITION = .TRUE.
CROWN_FIRE_SPOTTING_PERCENT = 30
/

&MISCELLANEOUS
PATH_TO_GDAL = 'auto'
SCRATCH = './scratch'
MISCELLANEOUS_INPUTS_DIRECTORY = './inputs'
/
"""
RUNSH = """#!/bin/bash
mkdir -p ./outputs ./scratch
elmfire_1.1 ./inputs/elmfire.data
"""
FUEL_MODELS = Path.home() / "dev/wildfire/elmfire-bench/runs/tutorials/09-wui-euler-simple/inputs/fuel_models.csv"


def subset(drawn_keys: list[str]) -> list[str]:
    keys = sorted(drawn_keys)
    perm = np.random.default_rng(SEED).permutation(len(keys))
    return [keys[i] for i in perm[:N_SUBSET]]


def grid90(meta):
    g = meta["grid"]
    nx = int(math.floor(g["nx"] * g["cell"] / CELL))
    ny = int(math.floor(g["ny"] * g["cell"] / CELL))
    return dict(crs=g["crs"], x0=g["x0"], y1=g["y1"], nx=nx, ny=ny, cell=CELL)


def resample_nearest(a, g125, g90):
    """Nearest-neighbour from the 125 m grid to the 90 m grid (same origin)."""
    ys = ((np.arange(g90["ny"]) + 0.5) * CELL / g125["cell"]).astype(int)
    xs = ((np.arange(g90["nx"]) + 0.5) * CELL / g125["cell"]).astype(int)
    return a[np.ix_(np.clip(ys, 0, a.shape[0] - 1), np.clip(xs, 0, a.shape[1] - 1))]


def write(path, arr, g, dtype, count=1):
    prof = dict(driver="GTiff", width=g["nx"], height=g["ny"], count=count, crs=g["crs"],
                transform=from_origin(g["x0"], g["y1"], g["cell"], g["cell"]), dtype=dtype, nodata=-9999, compress="deflate")
    with rasterio.open(path, "w", **prof) as r:
        r.write(arr if count > 1 else arr[None].astype(dtype) if arr.ndim == 2 else arr)


def static_layers(fire_key, meta, rec):
    """Fuel, canopy and terrain at 90 m (cached per fire)."""
    fd = C.fire_dir(fire_key) / "elmfire_static.npz"
    if fd.exists():
        return dict(np.load(fd))
    g125, g90 = meta["grid"], grid90(meta)
    z = np.load(C.fire_dir(fire_key) / "inputs.npz")
    out = {}
    if rec["region"] == "US":
        v = I.lf_vintage(rec["year"])
        for lay in ("FBFM40", "CC", "CH", "CBH", "CBD"):
            out[lay] = I.lf_export(v, lay, g90, CELL)
        out["dem"] = I.lf_export("LF2020", "Elev", g90, CELL)
    else:
        code = resample_nearest(z["code"], g125, g90).astype(np.int16)
        out["FBFM40"] = code
        # canopy for the CFFDRS mode: the values of ELMFIRE's own FBP verification inputs (CC 60 %, CH 20 m)
        # on conifer and mixedwood cells [H]; CBH/CBD omitted so FBP's standard values apply (Guide 1.2.1)
        conif_mixed = ((code >= 1) & (code <= 7)) | (code >= 400) | np.isin(code, [40, 50, 60, 70, 80, 90])
        decid = np.isin(code, [11, 12, 13])
        out["CC"] = np.where(conif_mixed | decid, 60, 0).astype(np.int16)
        out["CH"] = np.where(conif_mixed, 200, 0).astype(np.int16)
        out["CBH"] = np.zeros_like(code); out["CBD"] = np.zeros_like(code)
        out["dem"] = I.warp_to_grid(MRDEM, g90, Resampling.average).astype(np.float32)
    dem = np.nan_to_num(out["dem"].astype(np.float32), nan=0.0)
    dzdy, dzdx = np.gradient(dem, CELL)
    out["slp"] = np.degrees(np.arctan(np.hypot(dzdx, dzdy))).astype(np.int16)
    out["asp"] = ((np.degrees(np.arctan2(-dzdx, dzdy)) + 180.0) % 360.0).astype(np.int16)
    out["dem"] = dem.astype(np.int16)
    np.savez_compressed(fd, **out)
    return out


def run_interval(fire_key, meta, rec, st, it, closed125, act125, wx, zone125) -> dict:
    g125, g90 = meta["grid"], grid90(meta)
    case = RUNROOT / fire_key.replace(":", "_") / f"{it['epoch']:05d}"
    if (case / "result.json").exists():
        return json.loads((case / "result.json").read_text())
    if case.exists():
        shutil.rmtree(case)
    (case / "inputs").mkdir(parents=True)
    closed = resample_nearest(closed125, g125, g90)
    act = resample_nearest(act125, g125, g90)
    edge = closed & ~ndimage.binary_erosion(closed)
    held = edge & ~ndimage.binary_dilation(act, iterations=1)
    ring = ndimage.binary_dilation(held, iterations=1) & ~closed
    fb = st["FBFM40"].copy()
    nb = 101 if rec["region"] == "CAN" else 99
    fb[held | ring] = nb
    t0, t1 = pd.Timestamp(it["t_start"]), pd.Timestamp(it["t_end"])
    hours = max(1, int(math.ceil((t1 - t0) / pd.Timedelta(hours=1))))
    nb_ = hours + 1
    zone = resample_nearest(zone125, g125, g90)
    ws = np.zeros((nb_, g90["ny"], g90["nx"]), np.float32); wd = ws.copy(); m1 = ws.copy()
    for zi, sid in enumerate(meta["zone_station"]):
        w = wx[sid]
        msk = zone == zi if len(meta["zone_station"]) > 1 else np.ones(zone.shape, bool)
        for b in range(nb_):
            tb = t0.floor("h") + pd.Timedelta(hours=b)
            row = w.loc[:tb].iloc[-1]
            ws[b][msk] = float(np.nan_to_num(row.ws, nan=0.0))
            wd[b][msk] = float(np.nan_to_num(row.wd, nan=0.0))
            m1[b][msk] = 147.2 * (101 - row.ffmc) / (59.5 + row.ffmc)
    for name, a in (("ws", ws), ("wd", wd), ("m1", m1), ("m10", m1 + 1.0), ("m100", m1 + 2.0)):
        write(case / "inputs" / f"{name}.tif", a, g90, "float32", count=nb_)
    phi = np.where(closed, -1.0, 1.0).astype(np.float32)
    write(case / "inputs" / "phi.tif", phi, g90, "float32")
    write(case / "inputs" / "adj.tif", np.ones_like(phi), g90, "float32")
    write(case / "inputs" / "fbfm.tif", fb.astype(np.int16), g90, "int16")
    for lay, name in (("CC", "cc"), ("CH", "ch"), ("CBH", "cbh"), ("CBD", "cbd"), ("dem", "dem"), ("slp", "slp"), ("asp", "asp")):
        write(case / "inputs" / f"{name}.tif", st[lay].astype(np.int16), g90, "int16")
    shutil.copy(FUEL_MODELS, case / "inputs" / "fuel_models.csv")
    cffdrs = ""
    canopy = "CBH_FILENAME = 'cbh'\nCBD_FILENAME = 'cbd'\n"
    if rec["region"] == "CAN":
        w = wx[meta["zone_station"][0]]
        noon = w.loc[:t0].dropna(subset=["dmc"]).iloc[-1]
        canopy = ""
        cffdrs = (f"SURFACE_SPREAD_MODEL = 'CFFDRS'\nDAILY_WEATHER_FILENAME = 'daily_weather.txt'\n"
                  f"START_DMC = {float(noon.dmc):.1f}\nSTART_DC = {float(noon.dc):.1f}\n")
        off = int(round(-rec["geometry"].centroid.x / 15.0))
        lines = ["DAILY,TEMP,RH,PRECIP"]
        for day in pd.date_range(t0.normalize() - pd.Timedelta(days=1), t1.normalize() + pd.Timedelta(days=1), freq="D"):
            nt = day + pd.Timedelta(hours=12 + off)
            if nt in w.index:
                r = w.loc[nt]
                rain = float(w.rain.loc[nt - pd.Timedelta(hours=23):nt].sum())
                lines.append(f"{day:%d/%m/%Y},{float(np.nan_to_num(r.temp, nan=20)):.1f},{float(np.nan_to_num(r.rh, nan=40)):.1f},{rain:.1f}")
        (case / "inputs" / "daily_weather.txt").write_text("\n".join(lines) + "\n")
    hoy = int((t0.floor("h") - pd.Timestamp(f"{t0.year}-01-01")) / pd.Timedelta(hours=1))
    (case / "inputs" / "elmfire.data").write_text(DECK.format(canopy=canopy, cffdrs=cffdrs, tstop=f"{hours * 3600.0:.1f}",
                                                              year=t0.year, hoy=hoy))
    (case / "01-run.sh").write_text(RUNSH)
    (case / "01-run.sh").chmod(0o755)
    name = f"elmfire-v8-{fire_key.replace(':', '-')}-{it['epoch']}"
    peak = [0.0]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            o = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name], capture_output=True, text=True).stdout
            v = o.split("/")[0].strip() if o else ""
            try:
                num = float(v[:-3]) * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024}[v[-3:]]
                peak[0] = max(peak[0], num)
            except Exception:
                pass
            stop.wait(3)
    th = threading.Thread(target=sample, daemon=True); th.start()
    s = time.time()
    p = subprocess.run(["docker", "run", "--rm", "--name", name, "--memory", "5g", "-u", "1000:1000", "-v", f"{case}:/work",
                        "-w", "/work", IMAGE, "bash", "-c", "./01-run.sh > run.log 2>&1; echo rc=$?"],
                       capture_output=True, text=True, timeout=6 * 3600)
    stop.set(); th.join(timeout=5)
    wall = time.time() - s
    log = (case / "run.log").read_text(errors="ignore") if (case / "run.log").exists() else ""
    flux = sorted((case / "outputs").glob("ember_flux_*.bil"))
    status = "ok"
    if CRASH in log:
        status = "fail: particle-mode crash string"
    elif "rc=0" not in p.stdout:
        status = f"fail: {p.stdout.strip()[-40:]}"
    elif not flux:
        status = "fail: no ember flux output"
    res = dict(fire_key=fire_key, epoch=it["epoch"], status=status, wall_s=wall, peak_mib=peak[0], cells=g90["nx"] * g90["ny"])
    if status == "ok":
        with rasterio.open(flux[-1]) as r:
            a = r.read(1).astype(np.float64)
        a[~np.isfinite(a) | (a < 0)] = 0.0
        np.save(case / "flux.npy", a.astype(np.float32))
        for f in (case / "outputs").glob("*"):
            if f.suffix in (".bil", ".hdr") and "ember_flux" not in f.name:
                f.unlink()
    for f in (case / "inputs").glob("*.tif"):
        f.unlink()  # inputs are rebuilt from the cached layers if needed; keep disk use bounded
    (case / "result.json").write_text(json.dumps(res))
    C.log_run("elmfire", f"{fire_key}#{it['epoch']}", status, wall, f"peak_mib={peak[0]:.0f}")
    return res


def block_scores(fire_key, meta, epoch) -> np.ndarray | None:
    """ELMFIRE score per candidate block of an interval (sum of 90 m ember flux by block), or None."""
    case = RUNROOT / fire_key.replace(":", "_") / f"{epoch:05d}"
    if not (case / "flux.npy").exists():
        return None
    a = np.load(case / "flux.npy")
    g = meta["grid"]
    bsz = g["cell"] * C.CAND_F
    ys = ((np.arange(a.shape[0]) + 0.5) * CELL // bsz).astype(int)
    xs = ((np.arange(a.shape[1]) + 0.5) * CELL // bsz).astype(int)
    bny, bnx = g["ny"] // C.CAND_F, g["nx"] // C.CAND_F
    B = np.zeros((bny, bnx))
    np.add.at(B, (np.clip(ys, 0, bny - 1)[:, None], np.clip(xs, 0, bnx - 1)[None, :]), a)
    z = np.load(C.fire_dir(fire_key) / "intervals" / f"{epoch:05d}.npz")
    return B[z["bj"], z["bi"]]


def run_fire(fire_key: str, assignment: str = "decision", max_intervals: int | None = None) -> list[dict]:
    fd = C.fire_dir(fire_key)
    meta = json.loads((fd / "meta.json").read_text())
    info = json.loads((fd / "intervals" / "_done.json").read_text())
    rec = I.fire_record(fire_key)
    st = static_layers(fire_key, meta, rec)
    g = meta["grid"]
    z = np.load(fd / "inputs.npz")
    per = gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(g["crs"]).iloc[0]
    d = epochs(rec, detections(fire_key, rec["year"], assignment), g["crs"], per)
    ci = np.clip(((d.x - g["x0"]) / g["cell"]).astype(int), 0, g["nx"] - 1).values
    cj = np.clip(((g["y1"] - d.y) / g["cell"]).astype(int), 0, g["ny"] - 1).values
    first_ep = np.full((g["ny"], g["nx"]), 1 << 30, np.int32)
    np.minimum.at(first_ep, (cj, ci), d.ep.values.astype(np.int32))
    wx = {s: pd.read_csv(fd / f"wx_{s}.csv", index_col=0, parse_dates=True) for s in set(meta["zone_station"])}
    out = []
    cs = g["cell"]
    for it in info["intervals"][:max_intervals]:
        i = it["epoch"]
        pts = first_ep < i
        dil = ndimage.distance_transform_edt(~pts) * cs <= DIL
        E = ndimage.distance_transform_edt(~dil) * cs <= CLOSE
        closed = (ndimage.distance_transform_edt(E) * cs > CLOSE - cs) | dil
        t0 = pd.Timestamp(it["t_start"])
        rec_det = (d.t <= t0) & (d.t >= t0 - pd.Timedelta(hours=12)) & d.nh
        recent = np.zeros(closed.shape, bool)
        recent[cj[rec_det.values], ci[rec_det.values]] = True
        edge = closed & ~ndimage.binary_erosion(closed)
        act = edge & (ndimage.distance_transform_edt(~recent) * cs <= ACTIVE_R) if recent.any() else np.zeros_like(edge)
        out.append(run_interval(fire_key, meta, rec, st, it, closed, act, wx, z["zone"]))
    return out


if __name__ == "__main__":
    C.guard()
    for k in sys.argv[1:]:
        try:
            r = run_fire(k)
            print(k, len(r), sum(x["status"] == "ok" for x in r), flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            C.log_run("elmfire", k, f"error {e!r}"[:200], 0)

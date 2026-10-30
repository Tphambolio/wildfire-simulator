#!/usr/bin/env python3
"""Build the frozen M8 parity fixture (engine/tests/spread/data/m8_parity/) from the v8 run data.

Purpose
    The engine port of the v8 ember model M8 (``firesim.spread.embers_m8``) must reproduce the v8
    reference (``scripts/validation/v8/intervals.py``: the ``run_fire`` interval loop and ``m8_score``)
    to float tolerance. This script re-derives, with the v8 code's own functions, the per-interval
    INPUTS of the M8 landing field for a few intervals of v8 decision fires (start-perimeter mask,
    active-edge mask, spread rate at each source, fuel type / percent conifer / WAF class / curing
    grids, zone weather), checks them against the interval file v8 wrote during the decision run
    (``intervals/<epoch>.npz``: the engine port fed these inputs must give the same A), and writes a
    minimal extract: only the arrays the parity test reads.

    No outcome is read or written: no jump, sealed file, ``final/`` or score file is opened. VIIRS
    detections are read only to rebuild the start perimeter and active edge exactly as v8 did; the
    fixture stores the derived masks, not detections.

Usage (local only; needs the v8 data folder and geopandas)
    PYTHONPATH=engine/src:scripts/validation capjob python3 scripts/validation/v8_m8_parity_fixture.py \
        NBAC:2018_780 MTBS:AZ3368910927620210616
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "validation"))
sys.path.insert(0, str(REPO / "engine" / "src"))

from v8 import common as C  # noqa: E402
from v8 import inputs as I  # noqa: E402
from v8 import intervals as V  # noqa: E402

from firesim.spread import embers_m8 as E  # noqa: E402

OUT = REPO / "engine" / "tests" / "spread" / "data" / "m8_parity"
N_PER_FIRE = 2


def capture(fire_key: str, want: int = N_PER_FIRE) -> list[dict]:
    """Interval inputs exactly as v8 ``run_fire`` builds them, for the first ``want`` intervals
    with active sources and a previous perimeter (so the observed spread-rate path is exercised)."""
    from firesim.fbp.calculator import calculate_foliar_moisture
    import geopandas as gpd

    fd = C.fire_dir(fire_key)
    meta = json.loads((fd / "meta.json").read_text())
    info = json.loads((fd / "intervals" / "_done.json").read_text())
    done_ep = {it["epoch"]: it for it in info["intervals"] if it["n_active"] > 0}
    rec = I.fire_record(fire_key)
    g = meta["grid"]
    ny, nx, cs = g["ny"], g["nx"], g["cell"]
    z = np.load(fd / "inputs.npz")
    code, curing, zone = z["code"], z["curing"], z["zone"]
    per = gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(g["crs"]).iloc[0]
    split = C.split_table().set_index("fire_key").assignment
    det = V.detections(fire_key, rec["year"], split[fire_key])
    d = V.epochs(rec, det, g["crs"], per)
    ci = np.clip(((d.x - g["x0"]) / cs).astype(int), 0, nx - 1).values
    cj = np.clip(((g["y1"] - d.y) / cs).astype(int), 0, ny - 1).values
    first_ep = np.full((ny, nx), 1 << 30, np.int32)
    np.minimum.at(first_ep, (cj, ci), d.ep.values.astype(np.int32))
    T = d.groupby("ep").t.min().values
    wx = {s: pd.read_csv(fd / f"wx_{s}.csv", index_col=0, parse_dates=True) for s in set(meta["zone_station"])}
    lat_c = float(rec["geometry"].centroid.y); lng_c = float(rec["geometry"].centroid.x)
    us_group = None
    if rec["region"] == "US":
        fb = z["fbfm40"]
        us_group = np.full(fb.shape, "N", object)
        for c_, gg in I.GROUP_OF_FBFM.items():
            us_group[fb == c_] = gg
    burnable_cell = ~np.isin(code, [0, 100, 101, 102, 103, 104, 105, 106]) & (code > 0)
    season = {gr: V.fbp_of_code(code, gr) for gr in (False, True)}
    season = {gr: (tid, pc, V.waf_class(tid, us_group)) for gr, (tid, pc) in season.items()}
    out = []
    prev_closed, prev_T = None, None
    for i in range(1, len(T)):
        L = (T[i] - T[i - 1]) / np.timedelta64(1, "h")
        pts = first_ep < i
        dil = ndimage.distance_transform_edt(~pts) * cs <= V.DIL
        Em = ndimage.distance_transform_edt(~dil) * cs <= V.CLOSE
        closed = (ndimage.distance_transform_edt(Em) * cs > V.CLOSE - cs) | dil
        if L > 12.0 or not closed.any():
            prev_closed, prev_T = closed, T[i - 1]
            continue
        if i in done_ep and prev_closed is not None and len(out) < want:
            t0, t1 = pd.Timestamp(T[i - 1]), pd.Timestamp(T[i])
            doy = t0.dayofyear
            names, pcs, wcls = season[150 <= doy <= 258]
            edge = closed & ~ndimage.binary_erosion(closed)
            rec_det = (d.t <= t0) & (d.t >= t0 - pd.Timedelta(hours=12)) & d.nh
            recent = np.zeros((ny, nx), bool)
            recent[cj[rec_det.values], ci[rec_det.values]] = True
            act = edge & (ndimage.distance_transform_edt(~recent) * cs <= V.ACTIVE_R) if recent.any() else np.zeros_like(edge)
            zinfo = [V.zone_weather(wx[sid], t0, t1) for sid in meta["zone_station"]]
            dtp = (T[i - 1] - prev_T) / np.timedelta64(1, "m")
            ros = E.source_ros(act, prev_closed, dtp, cs)
            gcur = (np.where(wcls == 0, curing, 1.0) if us_group is None
                    else np.where(np.isin(us_group, ["G", "U"]), curing, 1.0)).astype(np.float32)
            fmc = calculate_foliar_moisture(lat_c, lng_c, None, doy)
            v8 = np.load(fd / "intervals" / f"{i:05d}.npz")
            out.append(dict(fire_key=fire_key, region=rec["region"], epoch=i, hours=float(L), fmc=float(fmc),
                            cell=float(cs), closed=closed, act=act, burnable=burnable_cell, names=names,
                            pcs=pcs.astype(np.float32), wcls=wcls, gcur=gcur,
                            zone=zone.astype(np.int16), ros=ros, zinfo=zinfo,
                            v8={k: v8[k] for k in ("bj", "bi", "A", "zone", "u10", "m_ff")}))
        prev_closed, prev_T = closed, T[i - 1]
    return out


def check(c: dict) -> float:
    """Engine landing field vs the v8 interval file (max relative difference of A)."""
    zones = [E.ZoneWeather(u10=z["u10"], wd_from=z["wd_from"], ffmc=z["ffmc"], bui=z["bui"]) for z in c["zinfo"]]
    A, _ = E.landing_field(c["act"], c["ros"], c["names"], c["pcs"], c["wcls"], c["gcur"],
                           c["zone"] if len(zones) > 1 else None, zones, c["hours"], c["fmc"], c["cell"])
    Ab = E.block_sums(A, c["v8"]["bj"], c["v8"]["bi"])
    ref = c["v8"]["A"].astype(np.float64)
    return float(np.max(np.abs(Ab - ref)) / max(np.max(np.abs(ref)), 1e-300))


def write(c: dict, tag: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{tag}.npz"
    pk = lambda m: np.packbits(m.astype(bool), axis=None)  # noqa: E731
    np.savez_compressed(
        p, shape=np.array(c["closed"].shape), cell=c["cell"], hours=c["hours"], fmc=c["fmc"],
        closed=pk(c["closed"]), act=pk(c["act"]), burnable=pk(c["burnable"]),
        names=c["names"].astype(np.int8), pcs=c["pcs"], wcls=c["wcls"].astype(np.int8), gcur=c["gcur"],
        zone_grid=c["zone"], ros=c["ros"].astype(np.float64),
        z_u10=np.array([z["u10"] for z in c["zinfo"]]), z_wd_from=np.array([z["wd_from"] for z in c["zinfo"]]),
        z_ffmc=np.array([z["ffmc"] for z in c["zinfo"]]), z_bui=np.array([z["bui"] for z in c["zinfo"]]),
        v8_bj=c["v8"]["bj"], v8_bi=c["v8"]["bi"], v8_A=c["v8"]["A"], v8_zone=c["v8"]["zone"],
        v8_u10=c["v8"]["u10"], v8_m_ff=c["v8"]["m_ff"])
    return p


def main(keys):
    C.guard()
    manifest = []
    for n, k in enumerate(keys):
        for c in capture(k):
            err = check(c)
            tag = f"fire{n + 1}_interval{len([m for m in manifest if m['fire'] == n + 1]) + 1}"
            p = write(c, tag)
            manifest.append(dict(fire=n + 1, fire_key=k, region=c["region"], epoch=c["epoch"], file=p.name,
                                 sha256=hashlib.sha256(p.read_bytes()).hexdigest(), bytes=p.stat().st_size,
                                 grid=list(c["closed"].shape), n_active=int(c["act"].sum()),
                                 n_candidates=int(len(c["v8"]["bj"])), zones=len(c["zinfo"]),
                                 build_max_rel_diff_A=err))
            print(tag, k, c["epoch"], f"A max rel diff {err:.3e}", flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

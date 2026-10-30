"""PREREG_ML_EMBER_DEV section 10 (owner decision D3): the disclosed ERA5 weather arm, development only.

Wind source: ERA5 10 m wind (hourly) as served by the Open-Meteo historical archive API (models=era5, timezone GMT,
no account; CC BY 4.0) — the same endpoint the frozen v8 ``inputs.py`` already uses for ERA5 precipitation in the
station arm's FFMC. Two sampling points per fire, both static (no interval-level recomputation of the active
segments): (a) the zone-0 station location (``meta.stations`` of the zone station), (b) the run-box centre. Section 10
says "over the active segments"; the box centre is the disclosed proxy (ERA5's ~25 km cell makes the difference
small for boxes of 40-90 km).

What runs (training fires only, through the Guard):
  1. fetch and cache hourly wind for each fire over its [t0, t1] window (``era5/raw/``);
  2. interval mean 10 m wind speed and vector-mean direction per interval, with the frozen ``zone_weather``
     convention (observed hours within [t_start, t_end]);
  3. the section-9 step-1 refit (unbounded β_w, v8 priors) and step-2 count model with ERA5 U10 in place of the
     station U10 in the sufficient statistics (zone 0 only: multi-zone fires keep the station wind for zones > 0,
     disclosed), for both sampling points;
  4. the interval-level occurrence detector of control 2 with ERA5 weather in place of station weather (wind only).
Output: era5/era5_arm.json, era5/era5_intervals.parquet.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ml_ember import beta_w_check as B  # noqa: E402
from scripts.ml_ember import cv  # noqa: E402
from scripts.ml_ember import paths as P  # noqa: E402
from scripts.ml_ember.loader import default_guard  # noqa: E402
from scripts.validation.v8 import common as C  # noqa: E402

OUT = P.DATA_FOLDER / "era5"; RAW = OUT / "raw"
OPENMETEO = "https://archive-api.open-meteo.com/v1/archive"


def fetch(lat, lon, start, end, tag):
    p = RAW / f"{tag}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if not p.exists():
        q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}",
             "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms", "timezone": "GMT", "models": "era5"}
        for attempt in range(5):
            try:
                with urllib.request.urlopen(OPENMETEO + "?" + urllib.parse.urlencode(q), timeout=60) as r:
                    p.write_bytes(r.read()); break
            except Exception as e:  # noqa: BLE001
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))
        time.sleep(0.4)
    j = json.loads(p.read_text())["hourly"]
    return pd.DataFrame(dict(ws=j["wind_speed_10m"], wd=j["wind_direction_10m"]), index=pd.to_datetime(j["time"]), dtype=float)


def interval_wind(w: pd.DataFrame, t0, t1):
    s = w.loc[t0:t1]
    if not len(s):
        s = w.loc[:t1].tail(6)
    r = np.radians(s.wd.fillna(0).to_numpy()); u = s.ws.to_numpy()
    return float(np.nanmean(u)), float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def main() -> int:
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True); RAW.mkdir(exist_ok=True)
    log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    guard = default_guard(); fires = cv.table("fires"); iv = cv.table("intervals")
    from pyproj import Transformer
    rows = []
    for n, k in enumerate(fires.fire_key, 1):
        guard.assert_training(k); fd = C.FIRES / k.replace(":", "_"); meta = guard.read_json(fd / "meta.json")
        g = meta["grid"]; tr = Transformer.from_crs(g["crs"], "EPSG:4326", always_xy=True)
        cx, cy = g["x0"] + g["nx"] * g["cell"] / 2, g["y1"] - g["ny"] * g["cell"] / 2
        lon_c, lat_c = tr.transform(cx, cy)
        sid = meta["zone_station"][0]; st = next(s for s in meta["stations"] if s["sid"] == sid)
        st_lon, st_lat = tr.transform(st["x"], st["y"])  # meta.stations holds grid-CRS x, y
        ivf = iv[iv.fire_key == k]
        start = pd.Timestamp(ivf.t_start.min()).normalize() - pd.Timedelta(days=1); end = pd.Timestamp(ivf.t_end.max()).normalize() + pd.Timedelta(days=1)
        w_st = fetch(st_lat, st_lon, start, end, f"{k.replace(':', '_')}_station"); w_bx = fetch(lat_c, lon_c, start, end, f"{k.replace(':', '_')}_box")
        for r in ivf.itertuples():
            u_s, d_s = interval_wind(w_st, pd.Timestamp(r.t_start), pd.Timestamp(r.t_end)); u_b, d_b = interval_wind(w_bx, pd.Timestamp(r.t_start), pd.Timestamp(r.t_end))
            rows.append(dict(fire_key=k, epoch=int(r.epoch), u10_era5_station=u_s, wd_era5_station=d_s, u10_era5_box=u_b, wd_era5_box=d_b))
        if n % 20 == 0:
            log(n, "fires fetched")
    E = pd.DataFrame(rows); E.to_parquet(OUT / "era5_intervals.parquet", index=False)
    d = iv.merge(E, on=["fire_key", "epoch"])
    out = dict(n_intervals=int(len(d)), source="Open-Meteo historical archive, models=era5, hourly wind_speed_10m / wind_direction_10m (m/s), timezone GMT; CC BY 4.0",
               wind_comparison={})
    for c in ("u10_era5_station", "u10_era5_box"):
        out["wind_comparison"][c] = dict(corr_with_station_u10=float(np.corrcoef(d.u10_ms, d[c])[0, 1]), mean_station=float(d.u10_ms.mean()), mean_era5=float(d[c].mean()),
                                         median_abs_diff=float((d.u10_ms - d[c]).abs().median()))
    log("wind comparison", out["wind_comparison"])
    # section 9 steps 1-2 with ERA5 wind in zone 0
    ss = pd.read_parquet(cv.suffstats_path()); pos = cv.table("cells_fit"); pos = pos[pos.y == 1]
    for c in ("u10_era5_station", "u10_era5_box"):
        m = dict(zip(zip(E.fire_key, E.epoch), E[c]))
        ss2 = ss.copy(); z0 = ss2.zone == 0
        ss2.loc[z0, "u10"] = [m.get((f, e), u) for f, e, u in zip(ss2.fire_key[z0], ss2.epoch[z0], ss2.u10[z0])]
        pos2 = pos.copy(); pz0 = pos2.zone == 0
        pos2.loc[pz0, "u10_ms"] = [m.get((f, e), u) for f, e, u in zip(pos2.fire_key[pz0], pos2.epoch[pz0], pos2.u10_ms[pz0])]
        L = B.Lik(ss2, pos2)
        fit_b = L.fit(prior=True); fit_u = L.fit(b_w_bounds=(-5, 5), prior=True)
        prof = L.profile_bw(np.round(np.arange(-5, 5.001, 0.25), 2), prior=True)
        ss_m = ss2.assign(fuel_group=ss2.fuel_group.replace(-1, 0)); pos_m = pos2.assign(fuel_group=pos2.fuel_group.replace(-1, 0))
        fit_k = B.Lik(ss_m, pos_m).fit(b_w_bounds=(-5, 5), prior=True, kappa_groups=[1, 2])
        # count model with ERA5 wind
        b_fixed = L.fit(prior=True, fixed_bw=0.0)
        lam0 = B.Lik._lam(0.0, b_fixed["b_L"], L.S, L.u, L.m, None, L.g) * math.exp(b_fixed["b0"])
        tmp = ss2[["fire_key", "epoch", "u10"]].copy(); tmp["lam0"] = lam0; tmp["Stot"] = ss2[["S_s0", "S_s1", "S_s2", "S_l0", "S_l1", "S_l2"]].sum(axis=1).to_numpy(); tmp["uS"] = tmp.u10 * tmp.Stot
        agg = tmp.groupby(["fire_key", "epoch"]).agg(Lam=("lam0", "sum"), uS=("uS", "sum"), Stot=("Stot", "sum")); agg["U10w"] = agg.uS / agg.Stot.clip(lower=1e-300)
        dd = iv.set_index(["fire_key", "epoch"]).join(agg[["Lam", "U10w"]]).dropna(subset=["Lam"]).reset_index()
        from scipy.optimize import minimize
        n_ = dd.n_jumps.to_numpy(float); off = np.log(np.maximum(dd.Lam.to_numpy(float), 1e-300)); x = np.log1p(dd.U10w.to_numpy(float))
        nll = lambda v: float((np.exp(off + v[0] + v[1] * x) - n_ * (off + v[0] + v[1] * x)).sum())  # noqa: E731
        r = minimize(nll, [0.0, 0.0], method="L-BFGS-B"); gg = np.arange(-3, 3.001, 0.05)
        prof_g = np.array([minimize(lambda v: nll([v[0], g_]), [r.x[0]], method="L-BFGS-B").fun for g_ in gg]); ins = gg[2 * (prof_g - prof_g.min()) <= 3.841]
        out[c] = dict(step1_bounded=fit_b, step1_unbounded=fit_u, profile_ci95=prof["ci95"], step1_kappa_free=fit_k,
                      step2_gamma=float(r.x[1]), step2_gamma_ci95=(float(ins.min()), float(ins.max())))
        log(c, "b_w unbounded", round(fit_u["b_w"], 3), prof["ci95"], "gamma", round(float(r.x[1]), 3))
    # occurrence detector (control-2 form) with ERA5 vs station wind
    from scripts.ml_ember import controls as K
    folds = cv.outer_folds(fires); d["y_int"] = (d.n_jumps > 0).astype(np.int8)
    occ = {}
    for name, cols in (("station_wind_only", ["u10_ms", "wd_deg"]), ("era5_station_wind_only", ["u10_era5_station", "wd_era5_station"]), ("era5_box_wind_only", ["u10_era5_box", "wd_era5_box"])):
        occ[name] = {k_: v for k_, v in K.interval_control(d, cols, fires, folds).items() if k_ in ("AUC", "ci95", "AUC_region0", "AUC_region1")}
    out["occurrence_detector"] = occ; log("occurrence", {k_: round(v["AUC"], 3) for k_, v in occ.items()})
    out["finished_utc"] = pd.Timestamp.now("UTC").isoformat(); out["wall_seconds"] = time.time() - t0
    (OUT / "era5_arm.json").write_text(json.dumps(out, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

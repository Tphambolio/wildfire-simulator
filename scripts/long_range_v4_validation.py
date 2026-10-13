#!/usr/bin/env python3
"""Long-range embers v4: per-fire exposure from VIIRS, pre-fire building vulnerability, NIST
timed fronts for Camp; calibration on five training fires (leave-one-fire-out CV) and a
pre-registered test on held-out fires (``PREREG_LR4``). **Illustrative — not validated.**

v4 = v3 (``model="v3"`` engine) plus:
(1) source burn times = min(progression rule, earliest VIIRS detection within 750 m) and a
    per-cell ember-emission multiplier (local VIIRS FRP / 50 MW)^gamma (``LR.viirs_*``);
    Camp's progression rule is replaced by the NIST TN 2135 timed fronts;
(2) per-building survival logit s = logit s0 + b_canopy (canopy 30-100 m − 0.3) + b_code
    [year built >= 2008] (``LR.survival_v4``; pre-fire open data only);
(3) the wind-event end of v3.
Free values (s0, gamma, b_canopy, b_code; link scale and ember factor as v3) are chosen by
leave-one-fire-out CV over Eaton, Palisades, Camp, Tubbs and Woolsey. Outputs outside the repo.

Usage (repo root):
    PYTHONPATH=engine/src python3 scripts/long_range_v4_validation.py fit --out <dir>
    PYTHONPATH=engine/src python3 scripts/long_range_v4_validation.py score --fires A B C --out <dir> --fit-dir <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import jasper_structure_validation as J  # noqa: E402
import long_range_ember_validation as L1  # noqa: E402
import long_range_v2_validation as V2  # noqa: E402
import long_range_v3_validation as V3  # noqa: E402
import structure_step3_validation as S3  # noqa: E402
from firesim.structures import long_range as LR  # noqa: E402

CALFIRE_DIR = L1.CALFIRE_DIR
V4_DIR = CALFIRE_DIR / "derived" / "v4"
TRAINING = ("eaton", "palisades", "camp", "tubbs", "woolsey")
# ember factor 100 (v3 fit, [R37]; independently ~ Lee & Davidson 2010 pp.681-682: mean per-brand
# ignition 1.75e-3 per brand on a footprint ~ k of the footprint area in m2)
FIXED = {"ember_factor": 100.0}
# Grid revised 2026-10-12 BEFORE PREREG_LR4 after the literature review (Hakes et al. 2017 Part II):
# - b_code in {0, 1.9}: post-2001-code homes 2-3 % vs 13-17 % burned (p.495) ~ +1.9 logit (1.6-2.3)
# - canopy ring 0-30 m (closest available to the 0-9.1 m ring, 67 % vs 32 % destroyed, p.496,
#   ~ -1.46 logit), b_canopy in {0, -3, -6} per unit cover fraction (centred at 0.3)
# - link scale in {10, 160} m: separation evidence is 2-15 m (pp.486, 498, 500) vs v3's fitted 160 m
GRID = {"survival": [0.15, 0.3, 0.45], "frp_gamma": [0.0, 1.0], "b_canopy": [0.0, -3.0, -6.0],
        "b_code": [0.0, 1.9], "link_scale_m": [10.0, 160.0]}
CODE_YEAR = 2001  # Hakes et al. 2017 p.495 (post-2001 code)
CANOPY_RING = "canopy_0_30"  # Hakes et al. 2017 p.496 supports the near ring over 30-100 m
N_FIT, N_SCORE, N_SENS = 6, 50, 20
VIIRS_RADIUS_M = 750.0


def training_cfg(fire: str) -> dict:
    cfg = dict(V3.FIRES3.get(fire) or V2.FIRES2[fire])
    if fire == "camp":
        cfg["nist_fronts"] = "derived/v4/camp_nist_fronts.geojson"  # replaces the imposed axis rule
    return cfg


def _read_csv(path: Path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else np.nan
    except (TypeError, ValueError):
        return np.nan


_v3_load = V3.load_case_units


def load_case_units_v4(fire: str, cfg: dict, dry_run: bool):
    """Held-out hybrid rule: footprints + unmatched DINS points (169 m2 squares); the status
    file covers both (unmatched ids are 10,000,000 + OBJECTID); unsurveyed / inaccessible are not
    scored. Training fires as v3."""
    hyb = CALFIRE_DIR / "derived" / f"{fire}_units_unmatched_dins.gpkg"
    if not hyb.exists():
        return _v3_load(fire, cfg, dry_run)
    import geopandas as gpd

    fp = gpd.read_file(CALFIRE_DIR / "derived" / f"{fire}_units.gpkg")
    fp = fp[fp.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    pts = gpd.read_file(hyb)
    sq = S3._squares(pts.geometry.x.to_numpy(), pts.geometry.y.to_numpy(), cfg["square_m2"])
    ids = np.concatenate([fp.unit_id.to_numpy(np.int64), pts.unit_id.to_numpy(np.int64)])
    geoms = np.concatenate([np.asarray(fp.geometry.values, dtype=object), np.asarray(sq, dtype=object)])
    if dry_run:
        rng = np.random.default_rng(1)
        st = rng.choice(["Destroyed (>50%)", "No Damage", "UNSURVEYED"], size=len(ids), p=[0.4, 0.5, 0.1])
    else:
        sd = {int(float(r["unit_id"])): r["DAMAGE"] for r in _read_csv(CALFIRE_DIR / "derived" / f"{fire}_units_status.csv")}
        st = np.array([sd.get(int(i), "UNSURVEYED") for i in ids])
    return ids, geoms, st == "Destroyed (>50%)", ~np.isin(st, ["UNSURVEYED", "Inaccessible"])


V3.load_case_units = load_case_units_v4


def stop_minute_v4(fire: str, cfg: dict) -> float:
    """v4 run end: as v3, but only after a wind event (>= 20 km/h for >= 2 h) has occurred in
    the window, so non-wind-driven fires are not stopped at 3 h [H]."""
    src = cfg.get("stop_source") or V3.STOP_SOURCES.get(fire)
    if not src:
        return math.inf
    t, v = V3.station_wind(fire, src)
    thr = LR.V3_STOP_WIND_KMH
    windy = t[(v >= thr) & (t >= 0)]
    if len(windy) < 3:  # fewer than ~2 h of event-strength wind: no wind event, no stop
        return math.inf
    return LR.wind_event_end(t, v, thr, min_after=max(180.0, float(windy.min()) + 120.0))


class Case4(V3.Case):
    """v3 ``Case`` with VIIRS / NIST burn times, FRP emission scaling and unit covariates."""

    def __init__(self, fire, cfg, dry_run=False, n_members=N_FIT, gammas=(0.0, 1.0)):
        self.gammas = tuple(gammas)
        self._fire = fire
        self.viirs = self._load_viirs(fire)
        super().__init__(fire, cfg, dry_run=dry_run, n_members=n_members,
                         stop_src=cfg.get("stop_source"))
        self.stop = stop_minute_v4(fire, cfg)
        self.covariates = self._load_covariates(fire)

    # -- inputs
    def _load_viirs(self, fire):
        p = V4_DIR / f"{fire}_viirs.csv"
        if not p.exists():
            return None
        rows = [r for r in _read_csv(p) if str(r.get("confidence", "n")).lower()[:1] not in ("l",)]
        return {"lat": np.array([_num(r["lat"]) for r in rows]), "lon": np.array([_num(r["lon"]) for r in rows]),
                "frp": np.array([_num(r["frp"]) for r in rows]),
                "t": np.array([_num(r["minutes_from_ignition"]) for r in rows])}

    def adjust_burn_times(self, t_burn):
        cfg = self.cfg
        t = np.asarray(t_burn, float).copy()
        self.burn_time_source = "progression rule"
        nist = cfg.get("nist_fronts")
        if nist:  # Camp: NIST TN 2135 arrival points replace the rule (IDW of the 4 nearest) [H]
            from scipy.spatial import cKDTree

            feats = [f for f in json.load(open(CALFIRE_DIR / nist))["features"]
                     if int(f["properties"].get("arrival_250m", 0)) == 1]  # first arrival per 250 m cell
            la = np.array([f["geometry"]["coordinates"][1] for f in feats])
            ln = np.array([f["geometry"]["coordinates"][0] for f in feats])
            # NIST minutes are from 06:20 PST; the Camp window starts 70 min later (t0 = -70)
            tt = np.array([float(f["properties"]["minutes_start"]) for f in feats]) + cfg.get("t0", 0.0)
            x, y = self.frame.to_local(la, ln)
            d, i = cKDTree(np.column_stack([x, y])).query(np.column_stack([self.sx, self.sy]), k=min(4, len(tt)))
            w = 1.0 / np.maximum(d, 1.0) ** 2
            t = np.sum(w * tt[i], axis=1) / np.sum(w, axis=1)
            self.burn_time_source = "NIST TN 2135 fronts (IDW)"
        tp = cfg.get("timed_perimeters")
        if tp:  # hourly GOES perimeters (GOFER): first perimeter containing the cell [V data]
            import shapely
            from datetime import datetime, timezone

            t0u = datetime.strptime(cfg["t0_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            feats = json.load(open(CALFIRE_DIR / tp))["features"]
            tt = np.full(len(self.sx), np.inf)
            for f in sorted(feats, key=lambda f: f["properties"]["tUTC"]):
                tu = datetime.strptime(f["properties"]["tUTC"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                m = (tu - t0u).total_seconds() / 60.0
                if m > cfg["window"]:
                    break
                g = shapely.geometry.shape(f["geometry"])
                g = shapely.transform(g, lambda c: np.column_stack(self.frame.to_local(c[:, 1], c[:, 0])))
                todo = ~np.isfinite(tt)
                if not todo.any():
                    break
                inside = shapely.contains_xy(g, self.sx[todo], self.sy[todo])
                idx = np.nonzero(todo)[0][inside]
                tt[idx] = m
            hit = np.isfinite(tt)
            t = np.where(hit, tt, t)
            self.burn_time_source = f"timed perimeters ({int(hit.sum())} cells) + rule"
        v = self.viirs
        if v is not None and len(v["t"]):
            tw = v["t"] + float(cfg.get("t0", 0.0))  # minutes from ignition -> window minutes
            ok = np.isfinite(tw) & (tw >= -60.0) & (tw <= self.cfg["window"] + 1440.0)
            dx, dy = self.frame.to_local(v["lat"][ok], v["lon"][ok])
            self._det = (np.asarray(dx, float), np.asarray(dy, float), v["frp"][ok], tw[ok])
            tv = LR.viirs_burn_times(self.sx, self.sy, self._det[0], self._det[1], self._det[3], VIIRS_RADIUS_M)
            # a detection is at or after the burn (overpass sampling): it caps the rule's time [H]
            t = np.where(np.isfinite(tv), np.minimum(t, np.maximum(tv, 0.0)), t)
            self.viirs_cells = int(np.isfinite(tv).sum())
            self.burn_time_source += " + VIIRS cap"
        else:
            self._det = None
            self.viirs_cells = 0
        return t

    def emit_scale(self, gamma):
        if gamma == 0 or self._det is None:
            return None
        return LR.viirs_emit_scale(self.sx, self.sy, self._det[0], self._det[1], self._det[2], gamma,
                                   radius_m=VIIRS_RADIUS_M)

    def _load_covariates(self, fire):
        ids = np.asarray(self.u.building_id if getattr(self.u, "building_id", None) is not None else [], dtype=np.int64)
        n = len(self.u)
        can = np.full(n, np.nan)
        yb = np.full(n, np.nan)
        key = {"woolsey": [("woolsey_fp", 0), ("woolsey", 10_000_000)]}.get(fire, [(fire, 0)])
        pos = {int(u): k for k, u in enumerate(ids)} if len(ids) else {}
        for stem, off in key:
            pc = V4_DIR / f"{stem}_unit_canopy.csv"
            if pc.exists():
                for r in _read_csv(pc):
                    k = pos.get(int(float(r["unit_id"])) + off)
                    if k is not None:
                        can[k] = _num(r.get(CANOPY_RING))
            py = V4_DIR / f"{stem}_unit_yearbuilt.csv"
            if py.exists():
                for r in _read_csv(py):
                    k = pos.get(int(float(r["unit_id"])) + off)
                    if k is not None:
                        yb[k] = _num(r.get("year_built"))
        if np.nanmax(np.r_[can, [-1]]) > 1.5:  # percent → fraction
            can = can / 100.0
        return {"canopy_ring": can, "year_built": yb,
                "coverage": {"canopy": float(np.mean(np.isfinite(can[self.dom]))),
                             "year_built": float(np.mean(np.isfinite(yb[self.dom])))}}

    def run4(self, theta: dict, n_members: int, stop: bool = True):
        th = dict(FIXED)
        th.update({k: v for k, v in theta.items() if k in ("link_scale_m", "ember_factor")})
        s_unit = LR.survival_v4(theta["survival"], self.covariates["canopy_ring"],
                                self.covariates["year_built"], theta.get("b_canopy", 0.0),
                                theta.get("b_code", 0.0), code_year=CODE_YEAR)
        th["survival"] = float(theta["survival"])
        th["frp_gamma"] = float(theta.get("frp_gamma", 0.0))
        th["survival_unit"] = s_unit
        return self.run(th, n_members, stop=stop)


def block_scores(case, p):
    """Secondary: share destroyed per 250 m block (>= 3 scored buildings) vs mean P."""
    sm = case.sm
    bx = np.floor(case.u.x[sm] / 250.0).astype(int)
    by = np.floor(case.u.y[sm] / 250.0).astype(int)
    _, inv = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    inv = inv.ravel()
    n = np.bincount(inv)
    o = np.bincount(inv, weights=case.obs[sm].astype(float)) / n
    q = np.bincount(inv, weights=p[sm]) / n
    keep = n >= 3
    w = n[keep] / n[keep].sum()
    mse = float(np.sum(w * (q[keep] - o[keep]) ** 2))
    clim = float(np.sum(w * o[keep]))
    mse0 = float(np.sum(w * (clim - o[keep]) ** 2))
    r = float(np.corrcoef(q[keep], o[keep])[0, 1]) if keep.sum() > 2 else float("nan")
    return {"blocks": int(keep.sum()), "weighted_mse": mse, "skill_vs_climatology": 1 - mse / mse0 if mse0 else None,
            "pearson_r": r}


# ------------------------------------------------------------------------------------- fit


def fit(args):
    S3._guard_out(args.out)
    grid = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    table = {}
    for fire in (args.fires or TRAINING):
        case = Case4(fire, training_cfg(fire), n_members=N_FIT if not args.quick else 2)
        o = case.obs[case.sm]
        clim = float(o.mean())
        table[fire] = {"climatology_brier": L1.brier(np.full(len(o), clim), o), "stop_min": case.stop,
                       "viirs_cells": case.viirs_cells, "burn_time_source": case.burn_time_source,
                       "covariate_coverage": case.covariates["coverage"], "runs": []}
        for k, th in enumerate(grid if not args.quick else grid[:2]):
            t1 = time.time()
            p, pe, info = case.run4(th, len(case.members))
            rec = {**th, "brier": L1.brier(p[case.sm], o), "auc": L1.auc(p[case.sm], o),
                   "kappa": J.kappa_of(p[case.sm] >= 0.5, o), "sum_p": float(p[case.sm].sum()),
                   "ring_involved": float(p[~case.dom].sum()), "block": block_scores(case, p)}
            table[fire]["runs"].append(rec)
            print(f"{fire} {k:2d} {th} brier {rec['brier']:.4f} auc {rec['auc']:.3f} sumP {rec['sum_p']:.0f} "
                  f"| {time.time() - t1:.0f} s rss {L1.rss_gb():.2f} GB", flush=True)
        (args.out / "fit_v4.json").write_text(json.dumps({"table": table}, indent=1, default=float))
        del case
    fires = list(table)
    keys = [tuple(r[k] for k in GRID) for r in table[fires[0]]["runs"]]
    br = {f: np.array([r["brier"] for r in table[f]["runs"]]) for f in fires}
    cv = {}
    for f in fires:
        others = [g for g in fires if g != f]
        j = int(np.argmin(np.mean([br[g] for g in others], axis=0)))
        r = table[f]["runs"][j]
        cv[f] = {"theta": dict(zip(GRID, keys[j])), "brier_heldout": float(br[f][j]), "auc": r["auc"],
                 "bss_vs_base_rate": 1 - float(br[f][j]) / table[f]["climatology_brier"],
                 "block_skill": r["block"]["skill_vs_climatology"]}
    j = int(np.argmin(np.mean([br[g] for g in fires], axis=0)))
    final = {"theta": {**FIXED, **dict(zip(GRID, keys[j]))}, "mean_brier": float(np.mean([br[g][j] for g in fires]))}
    out = {"grid": GRID, "fixed": FIXED, "members": N_FIT, "table": table, "lofo_cv": cv, "final": final}
    (args.out / "fit_v4.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"lofo_cv": cv, "final": final}, indent=1, default=float))


# ------------------------------------------------------------------------------- scoring

_UNITS_HYBRID = ("footprints (Eaton rule) + DINS points that matched no footprint as 169 m2 "
                 "squares (unit_id 10,000,000 + OBJECTID); unsurveyed footprints excluded from scoring")
FIRES4 = {
    "glass": {
        "perimeter": "raw/frap_perimeter_glass.geojson", "footprints": None, "ring": True,
        "ring_footprints": "derived/ms_usv2_glass.geojsonl", "worldcover": None, "chm": None,
        "square_m2": 169.0, "h": 20.0, "units_rule": _UNITS_HYBRID,
        # 03:48 PDT 27 Sep 2020 ignition (CAL FIRE incident page, NWS Storm Data) to 08:00 PDT 28 Sep
        # (end of the NWS North Bay wind episode 07:40)
        "window": 1692.0, "t0": 0.0, "t0_utc": "2020-09-27 10:48", "origin": (38.563, -122.497),
        "axis_deg": 225.0,
        "anchors_axis": [((38.550, -122.505), 383.0), ((38.470, -122.580), 1073.0),
                         ((38.458, -122.636), 1102.0), ((38.450, -122.620), 1572.0)],
        "timed_perimeters": "derived/v4/glass_gofer.geojson",
        "wind_speed": (5.0, 12.0), "wind_from": (20.0, 60.0),  # Knoxville Creek median 18 mph, Hawkeye 11; NE
        "proxy": "C4", "proxy_alt": "C7", "lat": 38.52, "elev": 300.0, "doy": 271,
        "ffmc": None, "dmc": 100.0, "dc": 800.0,
        "ffmc_raws": ("ATLAS PEAK", "2020-09-27T10:00", "2020-09-28T15:00"), "raws_dir": "glass",
        "raws_glob": "raws_FEMS_*.csv", "step3_seeds": None,
        "stop_source": {"summary": "refs/glass/glass_wind_hourly_summary_PDT.csv",
                        "column": "HADS KNXC1 KnoxvilleCreek", "t0": "2020-09-27 03:48", "fmt": "%Y-%m-%d %H:%M:%S"},
    },
    "czu": {
        "perimeter": "raw/frap_perimeter_czu.geojson", "footprints": None, "ring": True,
        "ring_footprints": "derived/ms_usv2_czu.geojsonl", "worldcover": None, "chm": None,
        "square_m2": 169.0, "h": 20.0, "units_rule": _UNITS_HYBRID,
        # the main run: 15:00 PDT 18 Aug 2020 (incident commander: wind shift NW->NE) to 12:00 PDT 19 Aug
        "window": 1260.0, "t0": 0.0, "t0_utc": "2020-08-18 22:00", "origin": (37.237, -122.278),
        "axis_deg": 150.0, "anchors_axis": [((37.172, -122.222), 519.0)],  # Hwy 236 / Big Basin HQ 23:39
        "timed_perimeters": "derived/v4/czu_gofer.geojson",
        "wind_speed": (2.0, 8.0), "wind_from": (-45.0, 45.0),  # Ben Lomond median 4 mph; NNW vs NE disputed
        "proxy": "C7", "proxy_alt": "C4", "lat": 37.15, "elev": 300.0, "doy": 231,
        "ffmc": None, "dmc": 100.0, "dc": 800.0,
        "ffmc_raws": ("BEN LOMOND", "2020-08-18T23:00", "2020-08-19T20:00"), "raws_dir": "czu",
        "raws_glob": "raws_FEMS_*.csv", "step3_seeds": None,
        "stop_source": {"summary": "refs/czu/czu_wind_hourly_summary_PDT.csv", "column": "HADS BNDC1 BenLomond",
                        "t0": "2020-08-18 15:00", "fmt": "%Y-%m-%d %H:%M:%S"},
    },
    "northcomplex": {
        "perimeter": "raw/frap_perimeter_northcomplex.geojson", "footprints": None, "ring": True,
        "ring_footprints": "derived/ms_usv2_northcomplex.geojsonl", "worldcover": None, "chm": None,
        "square_m2": 169.0, "h": 40.0, "units_rule": _UNITS_HYBRID,
        # 22:00 PDT 7 Sep to 13:00 PDT 9 Sep 2020 (the wind-driven run to Berry Creek / Feather Falls)
        "window": 2340.0, "t0": 0.0, "t0_utc": "2020-09-08 05:00", "origin": (39.73, -121.21),
        "axis_deg": 225.0,
        "anchors_axis": [((39.59, -121.27), 1340.0), ((39.645, -121.40), 1380.0)],
        "timed_perimeters": "derived/v4/northcomplex_gofer.geojson",
        "wind_speed": (9.0, 18.0), "wind_from": (25.0, 65.0),  # Jarbo Gap median 27 mph, max 45; NE
        "proxy": "C7", "proxy_alt": "C4", "lat": 39.65, "elev": 700.0, "doy": 252,
        "ffmc": None, "dmc": 100.0, "dc": 800.0,
        "ffmc_raws": ("JARBO GAP", "2020-09-08T05:00", "2020-09-09T20:00"), "raws_dir": "northcomplex",
        "raws_glob": "raws_FEMS_*.csv", "step3_seeds": None,
        "stop_source": {"summary": "refs/northcomplex/northcomplex_wind_hourly_summary_PDT.csv",
                        "column": "HADS JBGC1 JarboGap", "t0": "2020-09-07 22:00", "fmt": "%Y-%m-%d %H:%M:%S"},
    },
}
PREREG_LR4 = {
    "date_fixed": "2026-10-13",
    "question": "Does v4 (v3 + VIIRS burn-time caps and FRP emission scaling, Camp NIST fronts, "
                "per-building survival from pre-fire canopy and building-code era, event-gated run "
                "end), with free values fitted on five training fires, give skilful, calibrated "
                "per-building destruction probabilities on three held-out fires?",
    "theta_final": {"ember_factor": 100.0, "survival": 0.3, "frp_gamma": 1.0, "b_canopy": -3.0,
                    "b_code": 1.9, "link_scale_m": 160.0},
    "theta_how": "grid GRID (72 sets) x 6 members on Eaton, Palisades, Camp, Tubbs, Woolsey; minimum "
                 "mean Brier over the five (fit_v4.json, 2026-10-13). Grid revised from the "
                 "literature review BEFORE this block (Hakes et al. 2017 Part II pp.495-496, 486-500; "
                 "Lee & Davidson 2010 pp.681-682): b_code {0, 1.9} with code year 2001, canopy ring "
                 "0-30 m, link scale {10, 160} m",
    "training_cv_disclosed": "leave-one-fire-out Brier skill vs each fold's base rate was negative "
                             "on every fold: Eaton -0.11 (AUC 0.58), Palisades -0.34 (0.51), Camp "
                             "-3.09 (0.53), Tubbs -3.60 (0.52), Woolsey -5.20 (0.51). Expectation: the "
                             "calibration condition fails again.",
    "fires": {
        "glass": "held out; CAL FIRE DINS; hybrid units; GOFER hourly GOES perimeters set burn times",
        "czu": "held out; weak RAWS winds (no wind event -> no run end); GOFER perimeters",
        "northcomplex": "held out, replaces Marshall 2021 (Boulder County damage data has no "
                        "undamaged class; refs/heldout_v4_selection.md); GOFER perimeters",
        "jasper": "not run (no digitised 2024 fire positions)",
    },
    "inputs_and_differences_disclosed": [
        "burn times: training fires use the progression rule capped by VIIRS (Camp: NIST TN 2135 "
        "arrival points); held-out fires use GOFER hourly GOES perimeters (Liu et al. 2024, ESSD "
        "16:1395) where they exist, then the rule, capped by VIIRS",
        "held-out canopy uses a fixed NDVI threshold 0.30 (median of the training fires' per-fire "
        "NAIP calibrations 0.25-0.30); NAIP 2020 pre-fire for all three",
        "no open pre-fire year-built data was joined for the held-out fires, so b_code does not act "
        "there (training: Eaton, Palisades, Woolsey only)",
        "held-out units: footprints + DINS points that matched no footprint (169 m2 squares); "
        "unsurveyed footprints not scored",
        "outcome-related material glimpsed in documents (aggregate loss totals only) is listed in "
        "refs/<fire>/DATA_QUALITY or the data agents' reports; nothing building-level or spatial",
        "batch-2 literature (Sardoy validity, Storey tail, f_s, SWUIFT thresholds) is NOT folded "
        "into v4 (owner decision); it goes to v5",
    ],
    "metrics": "as PREREG_LR3, plus the block-level secondary (share destroyed per 250 m block with "
               ">= 3 scored buildings vs mean P: weighted MSE skill vs climatology, Pearson r); "
               "the secondary never decides",
    "baselines": "as PREREG_LR3 (a Hamada from front contact, b windward 5 %, c Chen & McAneney, d "
                 "count-matched distance band, e observed base rate, e2 training rate 0.584)",
    "decision_rule": "PASS only if, pooled over Glass, CZU and North Complex (250 m blocks within "
                     "fires, 2,000 resamples), all four 95 % intervals exclude zero in v4's favour: "
                     "Brier(c) - Brier(v4) > 0; kappa(v4) - kappa(b) > 0; kappa(v4) - kappa(d) > 0; "
                     "Brier(e) - Brier(v4) > 0. Per-fire results reported, never decide.",
    "members": "50; wind default_rng([20261011, 1000 + m]); draws default_rng([20261012, m])",
    "sensitivity_20_members": "NO_COVARIATES, NO_STOP, NO_SURVIVAL",
}


def score(args):
    S3._guard_out(args.out)
    theta = json.loads(args.theta) if args.theta else (PREREG_LR4.get("theta_final") or
                                                        json.loads((args.fit_dir / "fit_v4.json").read_text())["final"]["theta"])
    pooled = {"p": [], "o": [], "blk": [], "base": {}}
    eps = 1.0 / (2 * N_SCORE)
    for fi, fire in enumerate(args.fires):
        cfg = FIRES4.get(fire) or training_cfg(fire)
        n = (args.members or N_SCORE) if not args.quick else 3
        t0 = time.time()
        case = Case4(fire, cfg, dry_run=args.dry_run, n_members=n, gammas=(float(theta.get("frp_gamma", 0.0)),))
        p, pe, info = case.run4(theta, n)
        sens = {}
        if not args.no_sens:
            for name, over in {"NO_COVARIATES": {"b_canopy": 0.0, "b_code": 0.0},
                               "NO_STOP": "stop", "NO_SURVIVAL": {"survival": 1e-6, "b_canopy": 0.0, "b_code": 0.0}}.items():
                th2 = dict(theta)
                if over == "stop":
                    sens[name], _, _ = case.run4(th2, min(n, N_SENS), stop=False)
                    continue
                th2.update(over)
                sens[name], _, _ = case.run4(th2, min(n, N_SENS))
        sm, o = case.sm, case.obs
        base = V3.baselines(case, int(np.sum(p[sm] >= 0.5)))
        base["e2_training_rate"] = np.full(len(o), TRAINING_RATE)
        blk = L1.blocks_of(case.u.x[sm], case.u.y[sm])
        os_ = o[sm]
        kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        res = {"fire": fire, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(), "theta": theta,
               "units_domain": case.n_dom, "units_ring": case.n_ring, "units_scored": int(sm.sum()),
               "observed_destroyed": int(os_.sum()), "wildland_ha": case.wildland_ha,
               "front_contact_units": int(np.isfinite(case.t_front).sum()), "stop_min": case.stop,
               "window": case.window, "viirs_cells": case.viirs_cells, "burn_time_source": case.burn_time_source,
               "covariate_coverage": case.covariates["coverage"],
               "scores": {"V4": V2.prob_scores(p[sm], os_, eps)},
               "sensitivity": {k: V2.prob_scores(v[sm], os_, eps) for k, v in sens.items()},
               "baselines": {k: V2.prob_scores(v[sm], os_, eps) for k, v in base.items()},
               "block_secondary": {"V4": block_scores(case, p), **{k: block_scores(case, v) for k, v in base.items()}},
               "false_alarms_outside": ({"ring_footprints": case.n_ring, "expected_involved": float(p[~case.dom].sum())}
                                        if case.n_ring else None),
               "tests": {"Brier_c_minus_v4": L1.boot_diff(blk, bri, p[sm], base["c_chen_mcaneney"][sm], os_),
                         "kappa_v4_minus_b": L1.boot_diff(blk, kap, p[sm], base["b_windward_edge"][sm], os_),
                         "kappa_v4_minus_d": L1.boot_diff(blk, kap, p[sm], base["d_distance_band"][sm], os_),
                         "Brier_e_minus_v4": L1.boot_diff(blk, bri, p[sm], base["e_climatology"][sm], os_)},
               "distance_bands": []}
        for lo, hi in ((0, 100), (100, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 1e9)):
            b = sm & (case.d_edge >= lo) & (case.d_edge < hi)
            res["distance_bands"].append({"band_m": f"{lo}-{hi:g}", "units": int(b.sum()),
                                          "observed_rate": float(o[b].mean()) if b.any() else None,
                                          "mean_P_v4": float(p[b].mean()) if b.any() else None,
                                          "mean_P_c": float(base["c_chen_mcaneney"][b].mean()) if b.any() else None})
        res["seconds"] = time.time() - t0
        res["max_rss_gb"] = L1.rss_gb()
        tag = "_DRYRUN" if args.dry_run else ""
        (args.out / f"results_lr4_{fire}{tag}.json").write_text(json.dumps(res, indent=1, default=float))
        np.savez_compressed(args.out / f"units_lr4_{fire}{tag}.npz", x=case.u.x, y=case.u.y, dom=case.dom, scored=sm,
                            obs=o, d_edge=case.d_edge, P_V4=p, **{f"S_{k}": v for k, v in sens.items()},
                            **{f"B_{k}": v for k, v in base.items()})
        pooled["p"].append(p[sm])
        pooled["o"].append(os_)
        pooled["blk"].append(blk + 100000 * fi)
        for k, v in base.items():
            pooled["base"].setdefault(k, []).append(v[sm])
        print(f"{fire}: brier {res['scores']['V4']['brier']:.4f} auc {res['scores']['V4']['auc']:.3f} | "
              f"{res['seconds'] / 60:.1f} min rss {L1.rss_gb():.2f} GB", flush=True)
        del case
    if len(args.fires) > 1:
        P = np.concatenate(pooled["p"])
        O = np.concatenate(pooled["o"])
        _, B = np.unique(np.concatenate(pooled["blk"]), return_inverse=True)
        base = {k: np.concatenate(v) for k, v in pooled["base"].items()}
        kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        tests = {"Brier_c_minus_v4": L1.boot_diff(B, bri, P, base["c_chen_mcaneney"], O),
                 "kappa_v4_minus_b": L1.boot_diff(B, kap, P, base["b_windward_edge"], O),
                 "kappa_v4_minus_d": L1.boot_diff(B, kap, P, base["d_distance_band"], O),
                 "Brier_e_minus_v4": L1.boot_diff(B, bri, P, base["e_climatology"], O)}
        dec = {"bss_vs_c_positive_ci": tests["Brier_c_minus_v4"]["p2.5"] > 0,
               "kappa_vs_b_positive_ci": tests["kappa_v4_minus_b"]["p2.5"] > 0,
               "kappa_vs_d_positive_ci": tests["kappa_v4_minus_d"]["p2.5"] > 0,
               "bss_vs_base_rate_positive_ci": tests["Brier_e_minus_v4"]["p2.5"] > 0}
        dec["pass"] = all(dec.values())
        out = {"fires": args.fires, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(), "theta": theta,
               "units_scored": int(len(O)), "observed_destroyed": int(O.sum()),
               "scores": {"V4": V2.prob_scores(P, O, eps)},
               "baselines": {k: V2.prob_scores(v, O, eps) for k, v in base.items()}, "tests": tests, "decision": dec}
        tag = "_DRYRUN" if args.dry_run else ""
        (args.out / f"results_lr4_pooled_{'_'.join(args.fires)}{tag}.json").write_text(json.dumps(out, indent=1, default=float))
        print(json.dumps({"tests": tests, "decision": dec}, indent=1, default=float))


TRAINING_RATE = (7195 + 5729 + 18803 + 5656 + 1643) / (13177 + 9851 + 23256 + 6013 + 14539)  # 0.584


def prereg_hash() -> str:
    blob = {"PREREG_LR4": PREREG_LR4, "FIRES4": FIRES4, "GRID": GRID, "FIXED": FIXED}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=float).encode()).hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("fit")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--quick", action="store_true")
    a.add_argument("--fires", nargs="+", default=None)
    b = sub.add_parser("score")
    b.add_argument("--fires", nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--fit-dir", type=Path, default=None)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--quick", action="store_true")
    b.add_argument("--no-sens", action="store_true")
    b.add_argument("--members", type=int, default=None)
    b.add_argument("--theta", default=None)
    sub.add_parser("prereg")
    args = ap.parse_args()
    if args.cmd == "fit":
        fit(args)
    elif args.cmd == "score":
        score(args)
    else:
        print(json.dumps(PREREG_LR4, indent=1, default=float))
        print(prereg_hash())

#!/usr/bin/env python3
"""Long-range embers v3: calibration on training fires (leave-one-fire-out CV) and a
pre-registered test on held-out fires.

Specification: ``docs/structure-spread-spec.md`` §6.5; engine ``firesim.structures.long_range``
(``model="v3"``). **Illustrative — not validated** until ``PREREG_LR3`` passes.

v3 = the v2 ember chain (wildland + urban embers, receptive-bed ignition, draws) with
(1) probabilistic building-to-building links p = exp(-separation / scale) up to 50 m (Hamada
timing kept), (2) building survival (an exposed building never becomes fully involved with
probability s), (3) an ember-efficiency factor on q, and (4) a wind-event end of the run.

Training fires (all disclosed): Eaton 2025, Palisades 2025, Camp 2018. Free parameters
(link scale, survival, ember factor) are chosen on a grid by leave-one-fire-out CV on mean
Brier; the final values are fitted on all three. Held-out fires (prepared blind, sealed): see
``PREREG_LR3``. Outputs outside the repository only.

Usage (repo root):
    PYTHONPATH=engine/src python3 scripts/long_range_v3_validation.py fit --out <dir>
    PYTHONPATH=engine/src python3 scripts/long_range_v3_validation.py score --fire tubbs --out <dir>
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
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import jasper_structure_validation as J  # noqa: E402
import long_range_ember_validation as L1  # noqa: E402
import long_range_v2_validation as V2  # noqa: E402
import structure_step3_validation as S3  # noqa: E402
from firesim.fbp.calculator import calculate_fbp, calculate_foliar_moisture  # noqa: E402
from firesim.structures import long_range as LR  # noqa: E402
from firesim.structures.spread import WindPeriod, burnout_minutes, hamada_spread  # noqa: E402
from firesim.structures.units import build_units  # noqa: E402

CALFIRE_DIR = L1.CALFIRE_DIR
SEED = 20261012
TRAINING = ("eaton", "palisades", "camp")
GRID = {"link_scale_m": [5.0, 10.0, 20.0, 40.0], "survival": [0.0, 0.15, 0.3, 0.45, 0.6],
        "ember_factor": [0.1, 0.3, 1.0, 3.0, 10.0]}
N_FIT, N_SCORE = 8, 50
N_SENS = 20
# pooled destroyed share of the training fires' scored units (Eaton 7,195/13,177, Palisades
# 5,729/9,851, Camp 18,803/23,256): a climatology that does not know the test fire's rate
TRAINING_RATE = (7195 + 5729 + 18803) / (13177 + 9851 + 23256)
SENSITIVITY = {"NO_SURVIVAL": {"survival": 0.0}, "NO_STOP": {"stop": False},
               "LINKS_CERTAIN": {"link_scale_m": 1e9}, "NO_URBAN": {"urban_source": False},
               "NO_EMBERS": {"ember_factor": 0.0}}

# --------------------------------------------------------------------------------------------
# Held-out fires and pre-registration (fixed 2026-10-12 before any Tubbs/Woolsey outcome was
# read; set-ups from refs/<fire>/prereg_inputs.json written by the blind data step: published
# documents and weather only, outcomes sealed).
# --------------------------------------------------------------------------------------------
FIRES3 = {
    "tubbs": {
        "perimeter": "raw/frap_perimeter_tubbs.geojson", "footprints": None,
        "ring_footprints": "derived/ms_usv2_tubbs.geojsonl", "ring": True, "worldcover": None,
        "chm": None, "square_m2": 169.0, "h": 20.0, "window": 495.0, "origin": (38.609, -122.629),
        "t0": 0.0, "axis_deg": 225.0,
        "anchors_axis": [((38.566, -122.683), 100.0), ((38.510, -122.750), 255.0), ((38.500, -122.770), 315.0)],
        "wind_speed": (8.0, 15.0), "wind_from": (25.0, 65.0), "proxy": "C7", "proxy_alt": "C4",
        "lat": 38.55, "elev": 250.0, "doy": 281, "ffmc": None, "dmc": 100.0, "dc": 800.0,
        "ffmc_raws": ("SANTA ROSA", "2017-10-09T05:00", "2017-10-09T13:00"), "raws_dir": "tubbs",
        "raws_glob": "raws_FEMS_*.csv", "step3_seeds": None,
        "units_rule": "camp (DINS points as 169 m2 squares; domain footprints are post-fire)",
    },
    "woolsey": {
        "perimeter": "raw/frap_perimeter_woolsey.geojson", "footprints": None,
        "ring_footprints": "derived/ms_usv2_woolsey.geojsonl", "ring": True, "worldcover": None,
        "chm": None, "square_m2": 169.0, "h": 40.0, "window": 1416.0, "origin": (34.235, -118.701),
        "t0": 0.0, "axis_deg": 210.0,
        "anchors_axis": [((34.18, -118.765), 396.0), ((34.143, -118.749), 889.0),
                         ((34.087, -118.785), 1041.0), ((34.035, -118.855), 1411.0)],
        "wind_speed": (10.0, 16.0), "wind_from": (25.0, 60.0), "proxy": "C4", "proxy_alt": "C2",
        "lat": 34.13, "elev": 350.0, "doy": 312, "ffmc": None, "dmc": 100.0, "dc": 800.0,
        "ffmc_raws": ("CHEESEBORO", "2018-11-09T00:00", "2018-11-10T00:00"), "raws_dir": "woolsey",
        "raws_glob": "raws_FEMS_*.csv", "step3_seeds": None,
        # Woolsey DINS has no 'No Damage' class (damaged structures only) and ~87 % of domain
        # footprints are pre-fire (2018-04..06): units = domain footprints (Eaton rule) plus the
        # DINS points no footprint matched (169 m2 squares); a footprint with no DINS point is
        # taken as undamaged [H] (DINS inspected damaged structures only), so it is scored 0.
        "units_rule": "woolsey_fp + unmatched DINS points; unsurveyed footprints = undamaged [H]",
    },
}
_HELDOUT_STOP = ({
    "tubbs": {"summary": "refs/tubbs/tubbs_wind_hourly_summary_PDT.csv", "column": "HADS HWKC1 Hawkeye",
              "t0": "2017-10-08 21:45", "fmt": "%Y-%m-%d %H:%M:%S"},
    "woolsey": {"summary": "refs/woolsey/woolsey_wind_hourly_summary_PST.csv", "column": "HADS CEEC1 Cheeseboro",
                "t0": "2018-11-08 14:24", "fmt": "%Y-%m-%d %H:%M:%S"},
})
PREREG_LR3 = {
    "date_fixed": "2026-10-12",
    "question": "Does v3 (v2 embers + probabilistic building-to-building links + survival + ember "
                "factor + wind-event end), with its free values fitted on the training fires, give "
                "skilful, calibrated per-building destruction probabilities on held-out fires?",
    "model": "LongRangeOptions(model='v3') with theta_final; 50 members; wind draws "
             "default_rng([20261011, 1000 + m]); member draws and thresholds default_rng([20261012, m])",
    "theta_final": {"link_scale_m": 160.0, "survival": 0.3, "ember_factor": 100.0},
    "theta_how": "grid over link_scale 5-160 m, survival 0-0.6, ember_factor 0.1-100 (stage 1 100 "
                 "sets + stage 2 27 sets, 8 members), minimum mean Brier over Eaton, Palisades and "
                 "Camp; this optimum lies at the grid edge for link_scale and ember_factor",
    "training_cv_disclosed": "leave-one-fire-out Brier skill vs each fold's base rate was negative "
                             "on every fold (stage 2: Eaton -0.30, Palisades -0.31, Camp -2.58). In-sample at "
                             "theta_final (20 members): Eaton Brier 0.257 vs climatology 0.248, "
                             "Palisades 0.262 vs 0.243, Camp 0.439 vs 0.155; AUC 0.57 / 0.52 / 0.41. "
                             "A logistic recalibration (Platt) and an exposure-weighted layer "
                             "(housing density within 100 m, edge distance) were tried by "
                             "leave-one-fire-out CV and also gave negative skill on every fold; the "
                             "pooled Platt slope on logit P was negative (Camp's inverted ranking). "
                             "They are not used. Expectation: the calibration condition will fail.",
    "fires": {"tubbs": "held out (DINS, Camp unit rule; partly not blind: an earlier session's "
                       "fetch script printed Tubbs damage counts on 2026-10-10 and aggregate loss "
                       "totals were glimpsed in reports; no building-level or spatial outcome seen)",
              "woolsey": "held out (units rule in FIRES3; aggregate loss totals glimpsed in the "
                         "CPUC report summary; nothing spatial)",
              "jasper": "not run (no digitised 2024 fire positions; disclosed)"},
    "metrics": "as PREREG_LR2 (Brier, BSS, log score, AUC, reliability, kappa at 0.5 and count-"
               "matched with seeded ties, distance bands, false alarms in the 1 km outside ring)",
    "baselines": {"a": "Hamada H30 from the imposed front contact at the wind midpoint",
                  "b": "upwind 5 % of domain units seeded + Hamada H30",
                  "c": "Chen & McAneney max(0, 0.6 (1 - d/700 m))",
                  "d": "count-matched distance band (seeded ties)",
                  "e": "climatology = the held-out fire's observed destroyed rate",
                  "e2": "training climatology 0.6855 (not deciding)"},
    "decision_rule": "PASS only if, pooled over Tubbs and Woolsey (250 m blocks within fires, "
                     "2,000 resamples), all four 95 % intervals exclude zero in v3's favour: "
                     "Brier(c) - Brier(v3) > 0; kappa(v3) - kappa(b) > 0; kappa(v3) - kappa(d) > 0; "
                     "Brier(e) - Brier(v3) > 0 (Brier skill vs base rate above 0). Per-fire results "
                     "are reported, never decide. No configuration may be promoted after scoring.",
    "sensitivity_20_members": "NO_SURVIVAL, NO_STOP, LINKS_CERTAIN, NO_URBAN, NO_EMBERS",
    "dry_run_2026_10_12": "random outcomes, 3 members (timing only): Tubbs 15,649 units (6,013 "
                          "domain + 9,636 ring), 12,666 ha source, 2,378 front-contact, wind-event "
                          "end 435 min (04:00 PDT); Woolsey 46,401 units (14,539 + 31,862), 33,479 ha, "
                          "4,741 front-contact, no wind-event end in the window; peak RSS 1.4 GB",
    "wind_event_end": "most exposed station's 10 m sustained wind (RAWS 20 ft x 1.15) < 20 km/h "
                      "for 2 h, >= 3 h after ignition: Tubbs Hawkeye (HADS HWKC1), Woolsey "
                      "Cheeseboro (HADS CEEC1)",
}
RAW_TO_10M = 1.15  # RAWS 20 ft (6.1 m) wind × 1.15 = 10 m wind (Andrews 2009 p.58) [H]

# Wind-event end: the most exposed station's sustained wind (10 m) below 20 km/h for 2 h,
# at least 3 h after the window start [H]. Station per fire (strongest median during the run).
STOP_SOURCES = {
    "eaton": {"summary": "refs/eaton/eaton_wind_hourly_summary_PST.csv", "column": "HENNINGER ",
              "t0": "2025-01-07 18:30", "fmt": "%Y-%m-%d %H:%M", "year": 2025},
    "palisades": {"summary": "refs/palisades/palisades_wind_hourly_summary_PST.csv",
                  "column": "HADS MBUC1 MalibuHills", "t0": "2025-01-07 10:30",
                  "fmt": "%Y-%m-%d %H:%M:%S"},
    "camp": {"mesowest": "refs/camp/mesowest_JBGC1_hourly_2018-11-07_to_11-09_PST.csv",
             "t0": "2018-11-08 07:30"},
}


STOP_SOURCES.update(_HELDOUT_STOP)


def _mph(cell: str):
    try:
        return float(cell.split("/")[0])
    except (ValueError, IndexError):
        return None


def station_wind(fire: str, src=None):
    """(minutes from the window start, 10 m sustained wind km/h) of the stop-rule station."""
    src = src or STOP_SOURCES[fire]
    t0 = datetime.strptime(src["t0"], "%Y-%m-%d %H:%M")
    ts, vs = [], []
    if "summary" in src:
        for row in csv.DictReader(open(CALFIRE_DIR / src["summary"])):
            v = _mph(row.get(src["column"], "") or "")
            if v is None:
                continue
            s = row[next(iter(row))]
            if src.get("year"):
                s = f"{src['year']}-{s}"
            t = datetime.strptime(s, src["fmt"])
            ts.append((t - t0).total_seconds() / 60.0)
            vs.append(v * 1.609 * RAW_TO_10M)
    else:
        for row in csv.DictReader(r for r in open(CALFIRE_DIR / src["mesowest"]) if not r.startswith("#")):
            t = datetime.strptime(row["datetime_PST"][:16], "%Y-%m-%d %H:%M")
            v = row.get("6.1m Wind Speed  mph")
            if not v:
                continue
            ts.append((t - t0).total_seconds() / 60.0)
            vs.append(float(v) * 1.609 * RAW_TO_10M)
    return np.array(ts), np.array(vs)


def stop_minute(fire: str, src=None) -> float:
    t, v = station_wind(fire, src)
    return LR.wind_event_end(t, v, LR.V3_STOP_WIND_KMH)


def load_case_units(fire: str, cfg: dict, dry_run: bool):
    """(ids, geoms lng/lat, destroyed, scored) per the fire's unit rule."""
    if fire != "woolsey":
        ids, geoms = L1.load_units(fire, cfg)
        obs, scored = L1.outcomes(fire, ids, dry_run)
        return ids, geoms, obs, scored
    import geopandas as gpd

    fp = gpd.read_file(CALFIRE_DIR / "derived" / "woolsey_fp_units.gpkg")
    fp = fp[fp.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    pts = gpd.read_file(CALFIRE_DIR / "derived" / "woolsey_units.gpkg")
    matched = set(int(x) for x in fp["OBJECTID"].dropna()) if "OBJECTID" in fp else set()
    extra = pts[~pts.unit_id.astype(int).isin(matched)]
    sq = S3._squares(extra.geometry.x.to_numpy(), extra.geometry.y.to_numpy(), cfg["square_m2"])
    ids = np.concatenate([fp.unit_id.to_numpy(np.int64), 10_000_000 + extra.unit_id.to_numpy(np.int64)])
    geoms = np.concatenate([np.asarray(fp.geometry.values, dtype=object), np.asarray(sq, dtype=object)])
    if dry_run:
        rng = np.random.default_rng(1)
        st = rng.choice(["Destroyed (>50%)", "Minor (10-25%)", "UNSURVEYED"], size=len(ids), p=[0.3, 0.3, 0.4])
    else:
        s1 = {int(r["unit_id"]): r["DAMAGE"] for r in csv.DictReader(open(CALFIRE_DIR / "derived" / "woolsey_fp_units_status.csv"))}
        s2 = {10_000_000 + int(r["unit_id"]): r["DAMAGE"] for r in csv.DictReader(open(CALFIRE_DIR / "derived" / "woolsey_units_status.csv"))}
        st = np.array([s1.get(int(i), s2.get(int(i), "UNSURVEYED")) for i in ids])
    obs = st == "Destroyed (>50%)"
    scored = st != "Inaccessible"  # unsurveyed footprints scored as undamaged [H]
    return ids, geoms, obs, scored


class Case:
    """Everything about one fire that does not depend on the v3 parameters."""

    def __init__(self, fire: str, cfg: dict, dry_run: bool = False, n_members: int = N_FIT,
                 stop_src=None):
        t0 = time.time()
        self.fire, self.cfg = fire, dict(cfg)
        cfg = self.cfg
        ids, geoms, obs_d, scored_d = load_case_units(fire, cfg, dry_run)
        if cfg.get("ring_footprints"):  # post-fire footprints: used outside the domain only
            ring = V2.ring_geoms(fire, dict(cfg, footprints=cfg["ring_footprints"]))
        else:
            ring = V2.ring_geoms(fire, cfg) if cfg.get("ring") else []
        n_dom = len(ids)
        allg = (np.concatenate([np.asarray(geoms, dtype=object), np.asarray(ring, dtype=object)])
                if ring else np.asarray(geoms, dtype=object))
        all_ids = np.concatenate([ids, -1 - np.arange(len(ring))]) if ring else ids
        u = build_units(allg, neighbour_cutoff_m=LR.V3_LINK_CUTOFF_M, ids=all_ids)
        self.u30 = build_units(allg, neighbour_cutoff_m=30.0, ids=all_ids)
        self.u, self.n_dom, self.n_ring = u, n_dom, len(ring)
        n = len(u)
        self.dom = np.zeros(n, bool)
        self.dom[:n_dom] = True
        self.obs = np.zeros(n, bool)
        self.obs[:n_dom] = obs_d
        self.sm = np.zeros(n, bool)
        self.sm[:n_dom] = scored_d
        frame = u.frame
        h = cfg["h"]
        if cfg.get("ffmc") is None:
            cfg["ffmc"] = V2.ffmc_from_raws(cfg)
        self.fmc = calculate_foliar_moisture(cfg["lat"], cfg["origin"][1], cfg["elev"], cfg["doy"])
        f_max = calculate_fbp(cfg["proxy"], cfg["wind_speed"][1] * 3.6, cfg["ffmc"], cfg["dmc"], cfg["dc"], fmc=self.fmc)
        kind = "crown" if f_max.cfb >= LR.CFB_CROWNING else "torch"
        pad = 2.0 * LR.cap_distance(kind, f_max.hfi, f_max.cfb, cfg["wind_speed"][1], cfg["proxy"]) + 1000.0
        raster = LR.Raster.around(u.x, u.y, pad, h)
        wl, _ = L1.wildland_mask(fire, cfg, frame, u.footprints[:n_dom], raster)
        rr, cc = np.nonzero(wl)
        self.sx = raster.x0 + (cc + 0.5) * h
        self.sy = raster.y_top - (rr + 0.5) * h
        self.t_burn = V2.burn_times(cfg, frame, self.sx, self.sy)
        self.window = cfg["window"]
        self.stop = stop_minute(fire, stop_src) if (stop_src or fire in STOP_SOURCES) else math.inf
        from scipy import ndimage

        dist_px, (ir, ic) = ndimage.distance_transform_edt(~wl, return_indices=True)
        ur, uc, ok = raster.index(u.x, u.y)
        urc, ucc = np.clip(ur, 0, raster.rows - 1), np.clip(uc, 0, raster.cols - 1)
        d_cell = np.where(ok, dist_px[urc, ucc] * h, np.inf)
        d_edge_raw = np.maximum(d_cell - u.size_m / 2.0 - h / 2.0, 0.0)
        t_grid = np.full(wl.shape, np.inf)
        t_grid[rr, cc] = self.t_burn
        nearest_t = np.where(ok, t_grid[ir[urc, ucc], ic[urc, ucc]], np.inf)
        tf = np.where(d_edge_raw <= L1.FRONT_CONTACT_M, np.maximum(nearest_t, 0.0), np.inf)
        tf[tf > self.window] = np.inf
        self.t_front = tf
        self.d_edge = np.maximum(d_edge_raw - L1.WILDLAND_MIN_DIST_M, 0.0)
        del dist_px, ir, ic, t_grid
        self.raster = raster
        self.d66 = burnout_minutes(150)
        self.wildland_ha = float(len(self.sx) * h * h / 1e4)
        self.members = []
        self.add_members(n_members)
        self.setup_s = time.time() - t0
        print(f"{fire}: units {n} (domain {n_dom}, ring {len(ring)}) scored {int(self.sm.sum())} | "
              f"wildland {self.wildland_ha:.0f} ha | front {int(np.isfinite(tf).sum())} | stop "
              f"{self.stop:.0f} min of {self.window:.0f} | {self.setup_s:.0f} s rss {L1.rss_gb():.2f} GB",
              flush=True)

    def sources(self, proxy, u_ms, dmc, dc):
        cfg, h = self.cfg, self.cfg["h"]
        f = calculate_fbp(proxy, u_ms * 3.6, cfg["ffmc"], dmc, dc, fmc=self.fmc)
        n = len(self.sx)
        cross = h / max(f.ros_final, 1e-3)
        return LR.EmberSourceCells(self.sx, self.sy, self.t_burn, self.t_burn + cross, np.full(n, f.hfi),
                                   np.full(n, f.sfc), np.full(n, f.cfc), np.full(n, f.cfb),
                                   np.full(n, proxy, dtype=object), h)

    def add_members(self, n_total: int) -> None:
        opts = LR.LongRangeOptions(members=1, seed=SEED, raster_m=self.cfg["h"], model="v3")
        for m in range(len(self.members), n_total):
            u_ms, wfrom = L1.member_wind(self.cfg, m)
            wind = [(0.0, u_ms * 3.6, wfrom)]
            fld = LR.landing_field(self.sources(self.cfg["proxy"], u_ms, self.cfg["dmc"], self.cfg["dc"]),
                                   wind, self.window, opts, raster=self.raster)
            landed = LR.landed_on_buildings(self.u, fld, opts).astype(np.float32)
            urban = LR.UrbanSource.build(self.u, wind, opts, LR.URBAN_RASTER_M)
            self.members.append({"m": m, "u_ms": u_ms, "wfrom": wfrom, "wind": wind, "edges": fld.edges,
                                 "landed": landed, "urban": urban})
            del fld

    def run(self, theta: dict, n_members: int, stop: bool = True, **over):
        """P(involved) over ``n_members`` (and member-mean ember ignitions)."""
        opts = LR.LongRangeOptions(members=1, seed=SEED, raster_m=self.cfg["h"], model="v3", **theta, **over)
        inv, emb, info = [], [], []
        for mem in self.members[:n_members]:
            rng = np.random.default_rng([SEED, mem["m"]])
            w = [WindPeriod(0.0, mem["u_ms"] * 3.6, mem["wfrom"])]
            t, s, lr, _, draw = LR.run_member_v3(self.u, self.t_front, mem["landed"], mem["edges"], opts, w,
                                                 self.window, rng, mem["urban"], mem["wind"],
                                                 burnout_min=self.d66, fb=1.0,
                                                 stop_min=self.stop if stop else math.inf)
            inv.append(t <= self.window)
            emb.append(lr)
            info.append(draw.info)
        return np.mean(inv, axis=0), np.mean(emb, axis=0), info


def brier_on(case: Case, p):
    return L1.brier(p[case.sm], case.obs[case.sm])


# ------------------------------------------------------------------------------------- fit


def fit(args) -> None:
    """Grid evaluation on the training fires, then leave-one-fire-out selection. ``--grid``
    (JSON dict of lists) replaces GRID; ``--merge`` adds the runs of an earlier fit_grid.json
    so that selection is over the union of the evaluated parameter sets."""
    S3._guard_out(args.out)
    grid_def = json.loads(args.grid) if args.grid else GRID
    grid = [dict(zip(grid_def, v)) for v in itertools.product(*grid_def.values())]
    table = {}
    for fire in TRAINING:
        cfg = V2.FIRES2[fire]
        case = Case(fire, cfg, n_members=N_FIT if not args.quick else 2)
        nm = len(case.members)
        clim = float(case.obs[case.sm].mean())
        table[fire] = {"climatology_brier": L1.brier(np.full(int(case.sm.sum()), clim), case.obs[case.sm]),
                       "stop_min": case.stop, "window": case.window, "runs": []}
        for k, th in enumerate(grid if not args.quick else grid[:2]):
            t1 = time.time()
            p, pe, info = case.run(th, nm)
            o = case.obs[case.sm]
            rec = {**th, "brier": brier_on(case, p), "kappa": J.kappa_of(p[case.sm] >= 0.5, o),
                   "sum_p": float(p[case.sm].sum()), "ring_involved": float(p[~case.dom].sum()),
                   "urban_ign_per_involved": float(np.mean([i["ember_ignitions_urban"] / max(i["involved"], 1) for i in info]))}
            table[fire]["runs"].append(rec)
            print(f"{fire} {k:2d} {th} brier {rec['brier']:.4f} kappa {rec['kappa']:+.3f} sumP "
                  f"{rec['sum_p']:.0f} ring {rec['ring_involved']:.0f} | {time.time() - t1:.0f} s "
                  f"rss {L1.rss_gb():.2f} GB", flush=True)
        (args.out / "fit_grid.json").write_text(json.dumps(table, indent=1, default=float))
        del case
    # leave-one-fire-out over the union with an earlier grid
    if args.merge:
        old = json.loads(Path(args.merge).read_text())["table"]
        for f in TRAINING:
            seen = {tuple(r[k] for k in GRID) for r in table[f]["runs"]}
            table[f]["runs"] = [r for r in old[f]["runs"] if tuple(r[k] for k in GRID) not in seen] + table[f]["runs"]
    for f in TRAINING:
        table[f]["runs"].sort(key=lambda r: tuple(r[k] for k in GRID))
    keys = [tuple(r[k] for k in GRID) for r in table[TRAINING[0]]["runs"]]
    assert all([tuple(r[k] for k in GRID) for r in table[f]["runs"]] == keys for f in TRAINING)
    br = {f: np.array([r["brier"] for r in table[f]["runs"]]) for f in TRAINING}
    cv = {}
    for f in TRAINING:
        others = [g for g in TRAINING if g != f]
        j = int(np.argmin(np.mean([br[g] for g in others], axis=0)))
        cv[f] = {"theta": dict(zip(GRID, keys[j])), "brier_heldout": float(br[f][j]),
                 "bss_vs_climatology": 1 - float(br[f][j]) / table[f]["climatology_brier"],
                 "brier_best_in_grid": float(br[f].min())}
    j = int(np.argmin(np.mean([br[g] for g in TRAINING], axis=0)))
    final = {"theta": dict(zip(GRID, keys[j])),
             "mean_brier": float(np.mean([br[g][j] for g in TRAINING]))}
    union = {k: sorted({r[k] for r in table[TRAINING[0]]["runs"]}) for k in GRID}
    out = {"grid": union, "members": N_FIT, "table": table, "lofo_cv": cv, "final": final}
    (args.out / "fit_grid.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"lofo_cv": cv, "final": final}, indent=1, default=float))


# ------------------------------------------------------------------------------- scoring


def density100(units) -> np.ndarray:
    """Buildings within 100 m of each unit (Knapp et al. 2021's strongest pre-fire predictor)."""
    from scipy.spatial import cKDTree

    tree = cKDTree(np.column_stack([units.x, units.y]))
    return np.array([len(v) - 1 for v in tree.query_ball_point(np.column_stack([units.x, units.y]), 100.0)])


def baselines(case: Case, n_model_pred: int):
    """(a) Hamada H30 from front contact, (b) windward 5 % + Hamada H30, (c) Chen & McAneney,
    (d) count-matched distance band (seeded ties), (e) climatology (observed rate)."""
    u30, sm, obs = case.u30, case.sm, case.obs
    w_mid = float(np.mean(case.cfg["wind_speed"])), float(np.mean(case.cfg["wind_from"]))
    wind3 = [WindPeriod(0.0, w_mid[0] * 3.6, w_mid[1])]
    pa = (hamada_spread(u30, case.t_front, wind3, duration_min=case.window,
                        burnout_min=case.d66).t_min <= case.window).astype(float)
    to = math.radians(w_mid[1] + 180.0)
    proj = u30.x * math.sin(to) + u30.y * math.cos(to)
    di = np.nonzero(case.dom)[0]
    band = np.zeros(len(u30), bool)
    band[di[np.argsort(proj[di], kind="stable")[:int(math.ceil(0.05 * case.n_dom))]]] = True
    pb = (hamada_spread(u30, np.where(band, 0.0, np.inf), wind3, duration_min=case.window,
                        burnout_min=case.d66).t_min <= case.window).astype(float)
    pc = np.maximum(0.0, 0.6 * (1.0 - case.d_edge / 700.0))
    pdist = np.zeros(len(u30))
    idx = np.nonzero(sm)[0]
    pdist[idx[V2.tiebreak_top(-case.d_edge[idx], n_model_pred)]] = 1.0
    pe = np.full(len(u30), float(obs[sm].mean()))
    return {"a_hamada": pa, "b_windward_edge": pb, "c_chen_mcaneney": pc,
            "d_distance_band": pdist, "e_climatology": pe}


def score(args) -> None:
    S3._guard_out(args.out)
    fit_res = json.loads((args.fit_dir / "fit_grid.json").read_text())
    theta = (json.loads(args.theta) if args.theta else PREREG_LR3["theta_final"]
             if PREREG_LR3.get("theta_final") else fit_res["final"]["theta"])
    pooled = {"p": [], "o": [], "blk": [], "base": {}}
    per_fire = {}
    eps = 1.0 / (2 * N_SCORE)
    for fi, fire in enumerate(args.fires):
        cfg = FIRES3.get(fire) or V2.FIRES2[fire]
        n = (args.members or N_SCORE) if not args.quick else 3
        t0 = time.time()
        case = Case(fire, cfg, dry_run=args.dry_run, n_members=n)
        p, pe, info = case.run(theta, n)
        sens = {}
        ns = N_SENS if not args.quick else 2
        for name, over in (SENSITIVITY.items() if not args.no_sens else []):
            th2 = dict(theta)
            stop = True
            for k, v in over.items():
                if k == "stop":
                    stop = v
                else:
                    th2[k] = v
            sens[name], _, _ = case.run(th2, ns, stop=stop)
        sm, o = case.sm, case.obs
        n_pred = int(np.sum(p[sm] >= 0.5))
        base = baselines(case, n_pred)
        base["e2_training_rate"] = np.full(len(o), TRAINING_RATE)
        blk = L1.blocks_of(case.u.x[sm], case.u.y[sm])
        os_ = o[sm]
        kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        res = {"fire": fire, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(), "theta": theta,
               "units_domain": case.n_dom, "units_ring": case.n_ring, "units_scored": int(sm.sum()),
               "observed_destroyed": int(os_.sum()), "wildland_ha": case.wildland_ha,
               "front_contact_units": int(np.isfinite(case.t_front).sum()), "stop_min": case.stop,
               "window": case.window, "scores": {"V3": V2.prob_scores(p[sm], os_, eps)},
               "sensitivity": {k: V2.prob_scores(v[sm], os_, eps) for k, v in sens.items()},
               "baselines": {k: V2.prob_scores(v[sm], os_, eps) for k, v in base.items()},
               "false_alarms_outside": ({"ring_footprints": case.n_ring, "expected_involved": float(p[~case.dom].sum())}
                                        if case.n_ring else None),
               "members_info": info,
               "tests": {"Brier_c_minus_v3": L1.boot_diff(blk, bri, p[sm], base["c_chen_mcaneney"][sm], os_),
                         "kappa_v3_minus_b": L1.boot_diff(blk, kap, p[sm], base["b_windward_edge"][sm], os_),
                         "kappa_v3_minus_d": L1.boot_diff(blk, kap, p[sm], base["d_distance_band"][sm], os_),
                         "Brier_e_minus_v3": L1.boot_diff(blk, bri, p[sm], base["e_climatology"][sm], os_),
                         "Brier_e2_minus_v3": L1.boot_diff(blk, bri, p[sm], base["e2_training_rate"][sm], os_)},
               "distance_bands": []}
        for lo, hi in ((0, 100), (100, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 1e9)):
            b = sm & (case.d_edge >= lo) & (case.d_edge < hi)
            res["distance_bands"].append({"band_m": f"{lo}-{hi:g}", "units": int(b.sum()),
                                          "observed_rate": float(o[b].mean()) if b.any() else None,
                                          "mean_P_v3": float(p[b].mean()) if b.any() else None,
                                          "mean_P_c": float(base["c_chen_mcaneney"][b].mean()) if b.any() else None})
        res["seconds"] = time.time() - t0
        res["max_rss_gb"] = L1.rss_gb()
        tag = "_DRYRUN" if args.dry_run else ""
        (args.out / f"results_lr3_{fire}{tag}.json").write_text(json.dumps(res, indent=1, default=float))
        np.savez_compressed(args.out / f"units_lr3_{fire}{tag}.npz", x=case.u.x, y=case.u.y, dom=case.dom,
                            scored=sm, obs=o, d_edge=case.d_edge, P_V3=p, density100=density100(case.u),
                            **{f"S_{k}": v for k, v in sens.items()}, **{f"B_{k}": v for k, v in base.items()})
        per_fire[fire] = res
        pooled["p"].append(p[sm])
        pooled["o"].append(os_)
        pooled["blk"].append(blk + 100000 * fi)
        for k, v in base.items():
            pooled["base"].setdefault(k, []).append(v[sm])
        print(f"{fire}: brier {res['scores']['V3']['brier']:.4f} kappa {res['scores']['V3']['at_0.5']['kappa']:+.3f} "
              f"| {res['seconds'] / 60:.1f} min rss {L1.rss_gb():.2f} GB", flush=True)
        del case
    if len(args.fires) > 1 or args.pool:
        P = np.concatenate(pooled["p"])
        O = np.concatenate(pooled["o"])
        B = np.concatenate(pooled["blk"])
        _, B = np.unique(B, return_inverse=True)
        base = {k: np.concatenate(v) for k, v in pooled["base"].items()}
        kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        tests = {"Brier_c_minus_v3": L1.boot_diff(B, bri, P, base["c_chen_mcaneney"], O),
                 "kappa_v3_minus_b": L1.boot_diff(B, kap, P, base["b_windward_edge"], O),
                 "kappa_v3_minus_d": L1.boot_diff(B, kap, P, base["d_distance_band"], O),
                 "Brier_e_minus_v3": L1.boot_diff(B, bri, P, base["e_climatology"], O),
                 "Brier_e2_minus_v3": L1.boot_diff(B, bri, P, base["e2_training_rate"], O)}
        dec = {"bss_vs_c_positive_ci": tests["Brier_c_minus_v3"]["p2.5"] > 0,
               "kappa_vs_b_positive_ci": tests["kappa_v3_minus_b"]["p2.5"] > 0,
               "kappa_vs_d_positive_ci": tests["kappa_v3_minus_d"]["p2.5"] > 0,
               "bss_vs_base_rate_positive_ci": tests["Brier_e_minus_v3"]["p2.5"] > 0}
        dec["pass"] = all(dec.values())
        out = {"fires": args.fires, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(), "theta": theta,
               "units_scored": int(len(O)), "observed_destroyed": int(O.sum()),
               "scores": {"V3": V2.prob_scores(P, O, 1.0 / (2 * N_SCORE))},
               "baselines": {k: V2.prob_scores(v, O, 1.0 / (2 * N_SCORE)) for k, v in base.items()},
               "tests": tests, "decision": dec}
        tag = "_DRYRUN" if args.dry_run else ""
        (args.out / f"results_lr3_pooled_{'_'.join(args.fires)}{tag}.json").write_text(json.dumps(out, indent=1, default=float))
        print(json.dumps({"tests": tests, "decision": dec}, indent=1, default=float))


def prereg_hash() -> str:
    blob = {"PREREG_LR3": PREREG_LR3, "FIRES3": {k: {kk: vv for kk, vv in v.items() if kk != "step3_seeds"}
                                                  for k, v in FIRES3.items()}}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=float).encode()).hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("fit")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--quick", action="store_true")
    a.add_argument("--grid", default=None, help="JSON dict of parameter lists (replaces GRID)")
    a.add_argument("--merge", default=None, help="earlier fit_grid.json to merge")
    b = sub.add_parser("score")
    b.add_argument("--fires", nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--fit-dir", type=Path, required=True)
    b.add_argument("--dry-run", action="store_true", help="random outcomes; status files never read")
    b.add_argument("--quick", action="store_true")
    b.add_argument("--pool", action="store_true")
    b.add_argument("--no-sens", action="store_true")
    b.add_argument("--members", type=int, default=None)
    b.add_argument("--theta", default=None, help="JSON parameter dict")
    sub.add_parser("prereg")
    args = ap.parse_args()
    if args.cmd == "fit":
        fit(args)
    elif args.cmd == "score":
        score(args)
    else:
        print(json.dumps(PREREG_LR3, indent=1, default=float))
        print(prereg_hash())

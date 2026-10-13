#!/usr/bin/env python3
"""Long-range embers v5: two-mode ember transport, surface brands, threshold receptors and short
contact links; diagnosis of v3/v4's flat probabilities, leave-one-fire-out calibration on five
training fires and a pre-registered test on held-out fires (``PREREG_LR5``). **Illustrative —
not validated.**

v5 (engine ``LongRangeOptions.for_model("v5")``, spec §6.7) keeps v4's inputs (VIIRS burn times
and optional FRP emission scaling, NIST fronts for Camp, GOFER perimeters for the held-out
fires, per-building survival from pre-fire canopy and code era, the event-gated wind end) and
changes the structure of the ember chain:
(1) Sardoy short flaming mode at the 10 m canopy-top wind, truncated at q99.9, plus a long
    charring mode (median 6.3 km, σ_ln 0.18; 27.6 % of torching/crowning brands; Sardoy 2008
    p.486) weighted 0.0535 (Storey et al. 2020 p.7: 2 % of spot fires beyond 3 km), × 3 when
    FFMC >= 96.75 (fine fuel moisture < 4 %, Cruz et al. 2012 p.281);
(2) surface-fuel brands f_s = 0.007 in mass (Filkov et al. 2017 Table 1);
(3) receptor ignition when the brand mass on a 1.44 m² receptor reaches a threshold M* (k-hit
    Poisson; bounds 3.5-91 g from SWUIFT p.8, FSJ104686 ψ*, Suzuki et al. 2015), fitted;
(4) short contact links p = exp(−sep / λ_r), λ_r 6 or 10 m (Lee & Davidson 2010b p.692; Hakes
    et al. 2017 pp.486, 498, 500), branding carried by the urban ember source.

Usage (repo root; outputs outside the repo):
    PYTHONPATH=engine/src python3 scripts/long_range_v5_validation.py diagnose --out <dir>
    PYTHONPATH=engine/src python3 scripts/long_range_v5_validation.py fit --out <dir>
    PYTHONPATH=engine/src python3 scripts/long_range_v5_validation.py lofo --out <dir> --fit-dir <dir>
    PYTHONPATH=engine/src python3 scripts/long_range_v5_validation.py score --fires glass czu northcomplex --out <dir>
"""

from __future__ import annotations

import argparse
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

import long_range_v4_validation as V4  # noqa: E402  (patches V3.load_case_units: hybrid units)

V3, V2, L1, J, S3 = V4.V3, V4.V2, V4.L1, V4.J, V4.S3
from firesim.structures import long_range as LR  # noqa: E402
from firesim.structures.spread import WindPeriod  # noqa: E402

SEED = V3.SEED
TRAINING = V4.TRAINING  # eaton, palisades, camp, tubbs, woolsey (as v4)
HELDOUT = ("glass", "czu", "northcomplex")  # as v4 (V4.FIRES4)
N_FIT, N_LOFO, N_SCORE, N_SENS = 6, 20, 50, 20
CODE_YEAR = V4.CODE_YEAR  # 2001 (Hakes et al. 2017 p.495)
# Fixed from the literature before any fit (spec §6.7):
FIXED = {"b_code": 1.9,  # Hakes p.495 (IBHS 2007): 2-3 % vs 13-17 % burned -> +1.9 logit [D]
         "f_surface": LR.V5_F_SURFACE, "dry_factor": LR.V5_DRY_FACTOR}
# long-mode weight per kernel: Sardoy-located 0.0535, Storey-located 0.904 (spec §6.7)
LONG_IGNITION = {"sardoy": LR.V5_LONG_IGNITION, "storey": LR.V5_STOREY_LONG["long_ignition"]}
# Free values (grid fixed 2026-10-12 before the fit; bounds from the sources in spec §6.7):
GRID = {"threshold_g": [3.5, 4.6, 24.0, 91.0], "link_scale_m": [6.0, 10.0],
        "survival": [0.15, 0.3, 0.45], "b_canopy": [0.0, -3.0], "frp_gamma": [0.0, 1.0]}
V3_THETA = {"link_scale_m": 160.0, "survival": 0.3, "ember_factor": 100.0}  # [R37]
# Woolsey label rule (v5, fixed 2026-10-13 before PREREG_LR5; spec §6.7, report §2.4): DINS 2018
# recorded damaged structures only, and v3/v4 scored every footprint without a DINS point as
# undamaged (12,537 of 14,539 units). v5 keeps that assumption only where DINS teams surveyed:
# a footprint without a DINS point is scored (as undamaged) only if a DINS-surveyed structure
# lies within 100 m [H]; footprints within 20 m of a DINS point that matched no footprint are not
# scored (possibly the same building, double-labelled). Surveyed structures are all scored.
WOOLSEY_RULE = {"neighbourhood_m": 100.0, "duplicate_m": 20.0}
DENSITY_GRID = {"b_dens": [0.5, 1.0, 2.0], "survival": [0.15, 0.3, 0.45], "threshold_g": [3.5, 24.0]}
# density variant: the other values at the main (Sardoy + Storey kernels) leave-all-in selection
DENSITY_BASE = {"kernel": "storey", "link_scale_m": 10.0, "b_canopy": -3.0, "frp_gamma": 0.0}


def _woolsey_rule(ids, geoms, obs, scored, dry_run):
    """Apply ``WOOLSEY_RULE`` to the v3 Woolsey units (footprints + unmatched DINS squares)."""
    import csv

    import shapely
    from scipy.spatial import cKDTree

    if dry_run:
        return scored
    st = {int(r["unit_id"]): r["DAMAGE"] for r in
          csv.DictReader(open(L1.CALFIRE_DIR / "derived" / "woolsey_fp_units_status.csv"))}
    ids = np.asarray(ids, np.int64)
    extra = ids >= 10_000_000
    surveyed = extra | np.array([st.get(int(i), "UNSURVEYED") != "UNSURVEYED" for i in ids])
    c = shapely.centroid(np.asarray(geoms, dtype=object))
    lat0 = float(np.mean(shapely.get_y(c)))
    x = shapely.get_x(c) * 111_320.0 * math.cos(math.radians(lat0))
    y = shapely.get_y(c) * 110_574.0
    xy = np.column_stack([x, y])
    d_surv, _ = cKDTree(xy[surveyed]).query(xy, k=1)
    d_extra, _ = cKDTree(xy[extra]).query(xy, k=1) if extra.any() else (np.full(len(ids), np.inf), None)
    keep_uns = (~surveyed) & (d_surv <= WOOLSEY_RULE["neighbourhood_m"]) & (d_extra > WOOLSEY_RULE["duplicate_m"])
    return np.asarray(scored, bool) & (surveyed | keep_uns)


def load_case_units_v5(fire: str, cfg: dict, dry_run: bool):
    ids, geoms, obs, scored = V4.load_case_units_v4(fire, cfg, dry_run)
    if fire == "woolsey":
        scored = _woolsey_rule(ids, geoms, obs, scored, dry_run)
    return ids, geoms, obs, scored


V3.load_case_units = load_case_units_v5


class Case5(V4.Case4):
    """v4 ``Case4`` plus the v5 wildland fields (short and long mode per member and FRP
    exponent). ``v3_fields``: also keep v3's wildland field (diagnosis and the v3 comparator)."""

    def __init__(self, fire, cfg, dry_run=False, n_members=N_FIT, gammas=(0.0, 1.0), v3_fields=False,
                 kernels=("sardoy",)):
        self.v3_fields = v3_fields
        self.kernels = tuple(kernels)
        super().__init__(fire, cfg, dry_run=dry_run, n_members=n_members, gammas=gammas)

    def add_members(self, n_total: int) -> None:
        h = self.cfg["h"]
        o3 = LR.LongRangeOptions(members=1, seed=SEED, raster_m=h, model="v3")
        o5 = LR.LongRangeOptions.for_model("v5", members=1, seed=SEED, raster_m=h,
                                           f_surface=FIXED["f_surface"])
        for m in range(len(self.members), n_total):
            u_ms, wfrom = L1.member_wind(self.cfg, m)
            wind = [(0.0, u_ms * 3.6, wfrom)]
            src = self.sources(self.cfg["proxy"], u_ms, self.cfg["dmc"], self.cfg["dc"])
            short, long_, info = {}, {}, {}
            landed3 = {}
            for g in self.gammas:
                src.emit_scale = self.emit_scale(g)
                fld = LR.landing_field(src, wind, self.window, o5, raster=self.raster)
                short[g] = LR.landed_on_buildings(self.u, fld, o5).astype(np.float32)
                if "sardoy" in self.kernels:
                    long_[("sardoy", g)] = LR.landed_long_on_buildings(self.u, fld, o5).astype(np.float32)
                if "storey" in self.kernels:  # same short mode; Storey-located long component
                    o5s = LR.LongRangeOptions.for_model("v5", members=1, seed=SEED, raster_m=h,
                                                        f_surface=FIXED["f_surface"], **LR.V5_STOREY_LONG)
                    fs = LR.landing_field(src, wind, self.window, o5s, raster=self.raster)
                    long_[("storey", g)] = LR.landed_long_on_buildings(self.u, fs, o5s).astype(np.float32)
                    del fs
                info[g] = {"extrapolated_share": fld.extrapolated_share,
                           "short_emitted": fld.emitted_total, "long_emitted": fld.long_emitted,
                           "classes": fld.classes[:4]}
                edges = fld.edges
                del fld
                if self.v3_fields:
                    f3 = LR.landing_field(src, wind, self.window, o3, raster=self.raster)
                    landed3[g] = LR.landed_on_buildings(self.u, f3, o3).astype(np.float32)
                    del f3
            urban = LR.UrbanSource.build(self.u, wind, o3, LR.URBAN_RASTER_M)
            self.members.append({"m": m, "u_ms": u_ms, "wfrom": wfrom, "wind": wind, "edges": edges,
                                 "short": short, "long": long_, "info": info, "landed3": landed3,
                                 "urban": urban})

    def survival_units(self, theta):
        if getattr(self, "dens100", None) is None:
            self.dens100 = LR.building_density(self.u)
        return LR.survival_v5(float(theta["survival"]), self.dens100, self.covariates["canopy_ring"],
                              self.covariates["year_built"], float(theta.get("b_dens", 0.0)),
                              float(theta.get("b_canopy", 0.0)), float(theta.get("b_code", FIXED["b_code"])),
                              code_year=CODE_YEAR)

    def run5(self, theta: dict, n_members: int, stop: bool = True, wild: bool = True,
             urban: bool = True, links: bool = True, long_mode: bool = True):
        """P(involved by the window end) over ``n_members``; ``wild``/``urban``/``links``/
        ``long_mode`` switch components off (diagnosis)."""
        th = {**FIXED, **theta}
        g = float(th.get("frp_gamma", 0.0))
        kern = th.get("kernel", "sardoy")
        th.setdefault("long_ignition", LONG_IGNITION[kern])
        opts = LR.LongRangeOptions.for_model(
            "v5", members=1, seed=SEED, raster_m=self.cfg["h"], threshold_g=float(th["threshold_g"]),
            link_scale_m=float(th["link_scale_m"]) if links else 1e-9, survival=float(th["survival"]),
            long_ignition=float(th["long_ignition"]), dry_factor=float(th["dry_factor"]),
            f_surface=float(th["f_surface"]))
        k_long = LR.long_ignition_factor(opts, self.cfg["ffmc"]) if long_mode else 0.0
        s_unit = self.survival_units(th)
        fs_scale = 1.0 if abs(float(th["f_surface"]) - FIXED["f_surface"]) < 1e-12 else None
        if fs_scale is None:
            raise ValueError("f_surface is fixed at the field build (FIXED)")
        inv, emb, info = [], [], []
        for mem in self.members[:n_members]:
            rng = np.random.default_rng([SEED, mem["m"]])
            w = [WindPeriod(0.0, mem["u_ms"] * 3.6, mem["wfrom"])]
            we = (mem["short"][g] + k_long * mem["long"][(kern, g)]) if wild else None
            t, s, lr, _, draw = LR.run_member_v5(self.u, self.t_front, we, mem["edges"], opts, w,
                                                 self.window, rng, mem["urban"] if urban else None,
                                                 mem["wind"], burnout_min=self.d66, fb=1.0,
                                                 stop_min=self.stop if stop else math.inf,
                                                 survival_unit=s_unit)
            inv.append(t <= self.window)
            emb.append(lr)
            info.append(draw.info)
        return np.mean(inv, axis=0), np.mean(emb, axis=0), info

    def run3(self, theta: dict, n_members: int, survival_unit=None, stop: bool = True, gamma: float = 0.0):
        """v3 (or v4 with ``survival_unit``) on the v3 wildland field (``v3_fields``)."""
        opts = LR.LongRangeOptions(members=1, seed=SEED, raster_m=self.cfg["h"], model="v3",
                                   **{k: v for k, v in theta.items()
                                      if k in ("link_scale_m", "survival", "ember_factor")})
        inv = []
        for mem in self.members[:n_members]:
            rng = np.random.default_rng([SEED, mem["m"]])
            w = [WindPeriod(0.0, mem["u_ms"] * 3.6, mem["wfrom"])]
            t, *_ = LR.run_member_v3(self.u, self.t_front, mem["landed3"][gamma], mem["edges"], opts, w,
                                     self.window, rng, mem["urban"], mem["wind"], burnout_min=self.d66,
                                     fb=1.0, stop_min=self.stop if stop else math.inf,
                                     survival_unit=survival_unit)
            inv.append(t <= self.window)
        return np.mean(inv, axis=0)

    def run4(self, theta: dict, n_members: int, stop: bool = True):
        """v4 at ``theta`` (fit_v4.json) on the v3 field with v4's FRP exponent (the Case
        must hold that exponent in ``gammas``)."""
        s_unit = LR.survival_v4(theta["survival"], self.covariates["canopy_ring"],
                                self.covariates["year_built"], theta.get("b_canopy", 0.0),
                                theta.get("b_code", 0.0), code_year=CODE_YEAR)
        th = {**V4.FIXED, **{k: theta[k] for k in ("link_scale_m", "survival")}}
        return self.run3(th, n_members, survival_unit=s_unit, stop=stop,
                         gamma=float(theta.get("frp_gamma", 0.0)))


def training_cfg(fire):
    return V4.training_cfg(fire)


def _scores(p, o):
    return {"brier": L1.brier(p, o), "auc": L1.auc(p, o),
            "bss_vs_base_rate": 1 - L1.brier(p, o) / L1.brier(np.full(len(o), o.mean()), o),
            "kappa": J.kappa_of(p >= 0.5, o), "mean_p": float(p.mean()), "sd_p": float(p.std()),
            "iqr_p": float(np.subtract(*np.percentile(p, [75, 25])))}


# ------------------------------------------------------------------------------- diagnose


def diagnose(args):
    """Why is v3/v4's P flat? AUC of each component alone on the training fires (no fitting)."""
    S3._guard_out(args.out)
    from scipy.spatial import cKDTree

    out = {}
    th5 = {"threshold_g": 24.0, "link_scale_m": 6.0, "survival": 0.3, "b_canopy": 0.0, "b_code": 0.0}
    for fire in (args.fires or TRAINING):
        t0 = time.time()
        case = Case5(fire, training_cfg(fire), n_members=N_FIT if not args.quick else 2,
                     gammas=(0.0,), v3_fields=True)
        sm, o = case.sm, case.obs[case.sm]
        nm = len(case.members)
        u = case.u
        tree = cKDTree(np.column_stack([u.x, u.y]))
        xy = np.column_stack([u.x, u.y])
        comp = {
            "minus_d_edge": -case.d_edge,
            "density_100m": np.array([len(v) - 1 for v in tree.query_ball_point(xy, 100.0)], float),
            "density_30m": np.array([len(v) - 1 for v in tree.query_ball_point(xy, 30.0)], float),
            "front_contact": np.isfinite(case.t_front).astype(float),
            "minus_canopy_0_30": -case.covariates["canopy_ring"],
            "minus_year_built": -case.covariates["year_built"],
            "v3_wildland_embers": np.mean([m["landed3"][0.0].sum(axis=0) for m in case.members], axis=0),
            "v5_short_embers": np.mean([m["short"][0.0].sum(axis=0) for m in case.members], axis=0),
            "v5_long_embers": np.mean([m["long"][("sardoy", 0.0)].sum(axis=0) for m in case.members], axis=0),
        }
        runs = {
            "P_v3": case.run3(V3_THETA, nm),
            "P_v3_no_survival": case.run3({**V3_THETA, "survival": 0.0}, nm),
            "P_v3_no_embers": case.run3({**V3_THETA, "ember_factor": 0.0}, nm),
            "P_v5": case.run5(th5, nm)[0],
            "P_v5_no_survival": case.run5({**th5, "survival": 1e-9}, nm)[0],
            "P_v5_wildland_only": case.run5({**th5, "survival": 1e-9}, nm, urban=False, links=False)[0],
            "P_v5_urban_only": case.run5({**th5, "survival": 1e-9}, nm, wild=False, links=False)[0],
            "P_v5_links_only": case.run5({**th5, "survival": 1e-9}, nm, wild=False, urban=False)[0],
            "P_v5_no_long": case.run5(th5, nm, long_mode=False)[0],
        }
        rec = {"units_scored": int(sm.sum()), "destroyed_rate": float(o.mean()), "ffmc": case.cfg["ffmc"],
               "stop_min": case.stop, "members": nm, "covariate_coverage": case.covariates["coverage"],
               "sardoy_extrapolated_share": float(np.mean([m["info"][0.0]["extrapolated_share"] for m in case.members])),
               "member_wind_ms": [round(m["u_ms"], 2) for m in case.members],
               "kernel_classes": case.members[0]["info"][0.0]["classes"],
               "auc": {}, "spread": {}}
        blk = L1.blocks_of(u.x[sm], u.y[sm])
        for k, v in {**comp, **runs}.items():
            x = np.asarray(v, float)[sm]
            ok = np.isfinite(x)
            if ok.sum() < 50 or len(np.unique(o[ok])) < 2:
                rec["auc"][k] = None
                continue
            a = L1.auc(x[ok], o[ok])
            # 95 % block-bootstrap interval of AUC - 0.5
            ci = L1.boot_diff(blk[ok], L1.auc, x[ok], np.zeros(ok.sum()), o[ok]) if ok.all() else None
            rec["auc"][k] = {"auc": float(a), "coverage": float(ok.mean()),
                             "ci95": [0.5 + ci["p2.5"], 0.5 + ci["p97.5"]] if ci else None}
        for k, v in runs.items():
            p = v[sm]
            rec["spread"][k] = {"mean": float(p.mean()), "sd": float(p.std()),
                                "share_ge_0.95": float(np.mean(p >= 0.95)),
                                "share_le_0.05": float(np.mean(p <= 0.05)),
                                "brier": L1.brier(p, o)}
        out[fire] = rec
        np.savez_compressed(args.out / f"diag_{fire}.npz", x=u.x, y=u.y, scored=sm, obs=case.obs,
                            **{k: np.asarray(v, np.float32) for k, v in {**comp, **runs}.items()})
        print(fire, json.dumps({k: (round(v["auc"], 3) if v else None) for k, v in rec["auc"].items()}),
              f"| {time.time() - t0:.0f} s rss {L1.rss_gb():.2f} GB", flush=True)
        (args.out / "diagnosis.json").write_text(json.dumps(out, indent=1, default=float))
        del case


# ------------------------------------------------------------------------------------- fit


def fit(args):
    """Grid on the training fires for one long-mode kernel (``--kernel``: "sardoy", the primary
    Sardoy-located charring mode, or "storey", the Storey-located variant); ``select`` then
    chooses over both by leave-one-fire-out."""
    S3._guard_out(args.out)
    if args.variant == "density":
        args.kernel = DENSITY_BASE["kernel"]
        grid = [{**DENSITY_BASE, **dict(zip(DENSITY_GRID, v))} for v in itertools.product(*DENSITY_GRID.values())]
        prev = args.out / "fit_v5_density.json"
    else:
        grid = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
        prev = args.out / ("fit_v5.json" if args.kernel == "sardoy" else f"fit_v5_{args.kernel}.json")
    table = {}
    if prev.exists():
        table = json.loads(prev.read_text()).get("table", {})
    for fire in (args.fires or TRAINING):
        if fire in table and len(table[fire]["runs"]) == len(grid) and fire not in (args.redo or []):
            continue
        case = Case5(fire, training_cfg(fire), n_members=N_FIT if not args.quick else 2,
                     kernels=(args.kernel,) if args.kernel != "sardoy" else ("sardoy",))
        o = case.obs[case.sm]
        table[fire] = {"climatology_brier": L1.brier(np.full(len(o), o.mean()), o), "stop_min": case.stop,
                       "ffmc": case.cfg["ffmc"], "viirs_cells": case.viirs_cells,
                       "burn_time_source": case.burn_time_source,
                       "covariate_coverage": case.covariates["coverage"],
                       "sardoy_extrapolated_share": float(np.mean([m["info"][0.0]["extrapolated_share"]
                                                                   for m in case.members])),
                       "runs": []}
        for k, th in enumerate(grid if not args.quick else grid[:2]):
            t1 = time.time()
            p, _, info = case.run5({**th, "kernel": args.kernel}, len(case.members))
            rec = {**th, "kernel": args.kernel, **_scores(p[case.sm], o), "sum_p": float(p[case.sm].sum()),
                   "ring_involved": float(p[~case.dom].sum()), "block": V4.block_scores(case, p)}
            table[fire]["runs"].append(rec)
            print(f"{fire} {k:2d} {th} brier {rec['brier']:.4f} auc {rec['auc']:.3f} sumP {rec['sum_p']:.0f} "
                  f"| {time.time() - t1:.0f} s rss {L1.rss_gb():.2f} GB", flush=True)
        prev.write_text(json.dumps({"table": table}, indent=1, default=float))
        del case
    if args.variant == "density":
        select_tables({"storey": table}, args.out / "select_density.json", keyf=list(DENSITY_GRID) + ["kernel"],
                      base=DENSITY_BASE)
        return
    select_tables({args.kernel: table}, args.out / f"select_{args.kernel}.json")


def select(args):
    """Leave-one-fire-out selection over the union of the kernels' grids."""
    tables = {}
    for k in ("sardoy", "storey"):
        p = args.out / ("fit_v5.json" if k == "sardoy" else f"fit_v5_{k}.json")
        if p.exists():
            tables[k] = json.loads(p.read_text())["table"]
    select_tables(tables, args.out / "select_v5.json")


def select_tables(tables: dict, path: Path, keyf=None, base=None):
    table = {}
    for kern, tb in tables.items():
        for f, rec in tb.items():
            table.setdefault(f, {**{k: v for k, v in rec.items() if k != "runs"}, "runs": []})
            table[f]["runs"] += [{**r, "kernel": r.get("kernel", kern)} for r in rec["runs"]]
    keyf = keyf or (list(GRID) + ["kernel"])
    base = base or {}
    for f in table:
        table[f]["runs"].sort(key=lambda r: tuple(str(r[k]) for k in keyf))
    fires = [f for f in TRAINING if f in table]
    keys = [tuple(r[k] for k in keyf) for r in table[fires[0]]["runs"]]
    assert all([tuple(r[k] for k in keyf) for r in table[f]["runs"]] == keys for f in fires), "grids differ"
    br = {f: np.array([r["brier"] for r in table[f]["runs"]]) for f in fires}
    cv = {}
    for f in fires:
        others = [g for g in fires if g != f]
        j = int(np.argmin(np.mean([br[g] for g in others], axis=0)))
        r = table[f]["runs"][j]
        cv[f] = {"theta": {**base, **dict(zip(keyf, keys[j]))}, "brier_heldout": float(br[f][j]), "auc": r["auc"],
                 "bss_vs_base_rate": 1 - float(br[f][j]) / table[f]["climatology_brier"],
                 "block_skill": r["block"]["skill_vs_climatology"]}
    j = int(np.argmin(np.mean([br[g] for g in fires], axis=0)))
    final = {"theta": {**FIXED, **base, **dict(zip(keyf, keys[j]))},
             "mean_brier": float(np.mean([br[g][j] for g in fires])),
             "mean_auc": float(np.mean([table[g]["runs"][j]["auc"] for g in fires]))}
    best_auc = int(np.argmax(np.mean([[r["auc"] for r in table[g]["runs"]] for g in fires], axis=0)))
    out = {"grid": GRID, "kernels": list(tables), "fixed": FIXED, "members": N_FIT, "lofo_cv": cv, "final": final,
           "best_mean_auc_set": {"theta": {**base, **dict(zip(keyf, keys[best_auc]))},
                                 "mean_auc": float(np.mean([table[g]["runs"][best_auc]["auc"] for g in fires]))},
           "table": table}
    path.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"lofo_cv": cv, "final": final}, indent=1, default=float))


# --------------------------------------------------------------------- LOFO comparison


def lofo(args):
    """Training-fire comparison with block-bootstrap CIs: each fire scored at the v5 values
    chosen without it (fit_v5.json), against v3 at [R37] and v4 at its own leave-one-out
    values (fit_v4.json, frp_gamma 0 only), ``N_LOFO`` members each."""
    S3._guard_out(args.out)
    f5 = json.loads((args.fit_dir / args.select_file).read_text())
    f4 = json.loads(Path(args.v4_fit).read_text()) if args.v4_fit and Path(args.v4_fit).exists() else None
    eps = 1.0 / (2 * N_LOFO)
    res = {}
    for fire in (args.fires or TRAINING):
        th5 = f5["lofo_cv"][fire]["theta"]
        th4 = f4["lofo_cv"][fire]["theta"] if f4 and fire in f4.get("lofo_cv", {}) else None
        gs = sorted({0.0, float(th5.get("frp_gamma", 0.0)), float((th4 or {}).get("frp_gamma", 0.0))})
        case = Case5(fire, training_cfg(fire), n_members=N_LOFO, gammas=tuple(gs), v3_fields=True,
                     kernels=(th5.get("kernel", "sardoy"),))
        sm, o = case.sm, case.obs[case.sm]
        p5 = case.run5(th5, N_LOFO)[0]
        p3 = case.run3(V3_THETA, N_LOFO)
        p4 = case.run4(th4, N_LOFO) if th4 else None
        blk = L1.blocks_of(case.u.x[sm], case.u.y[sm])
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        r = {"theta_v5": th5, "theta_v4": th4, "units_scored": int(sm.sum()), "destroyed": int(o.sum()),
             "scores": {"v5": V2.prob_scores(p5[sm], o, eps), "v3": V2.prob_scores(p3[sm], o, eps)},
             "tests": {"brier_v3_minus_v5": L1.boot_diff(blk, bri, p5[sm], p3[sm], o),
                       "auc_v5_minus_v3": L1.boot_diff(blk, L1.auc, p5[sm], p3[sm], o),
                       "brier_base_minus_v5": L1.boot_diff(blk, bri, p5[sm], np.full(len(o), o.mean()), o),
                       "auc_v5_minus_half": L1.boot_diff(blk, L1.auc, p5[sm], np.full(len(o), 0.5), o)}}
        if p4 is not None:
            r["scores"]["v4"] = V2.prob_scores(p4[sm], o, eps)
            r["tests"]["brier_v4_minus_v5"] = L1.boot_diff(blk, bri, p5[sm], p4[sm], o)
            r["tests"]["auc_v5_minus_v4"] = L1.boot_diff(blk, L1.auc, p5[sm], p4[sm], o)
        res[fire] = r
        np.savez_compressed(args.out / f"lofo_{fire}.npz", x=case.u.x, y=case.u.y, scored=sm, obs=case.obs,
                            P_V5=p5, P_V3=p3, **({"P_V4": p4} if p4 is not None else {}))
        print(fire, {k: (round(v["brier"], 4), round(v["auc"], 3)) for k, v in r["scores"].items()},
              flush=True)
        (args.out / "lofo_v5.json").write_text(json.dumps(res, indent=1, default=float))
        del case


# ------------------------------------------------------------------------------- scoring

# Pooled destroyed share of the five training fires' scored units (Eaton 7,195/13,177, Palisades
# 5,729/9,851, Camp 18,803/23,256, Tubbs 5,656/6,013, Woolsey 1,643/4,816 under WOOLSEY_RULE)
TRAINING_RATE = (7195 + 5729 + 18803 + 5656 + 1643) / (13177 + 9851 + 23256 + 6013 + 4816)
PREREG_LR5: dict = {
    "date_fixed": "2026-10-13",
    "question": "Does v5 (two-mode ember transport, surface brands, threshold receptors, short contact "
                "links, pre-fire survival covariates), with free values chosen by leave-one-fire-out on "
                "five training fires, give skilful, calibrated per-building destruction probabilities on "
                "held-out fires, and does it rank buildings better than v3?",
    "primary": "main",
    "primary_rule": "the variant with the lowest leave-one-fire-out mean held-out-fold Brier (6 members) "
                    "among the pre-specified variants: main (Sardoy- and Storey-located long mode, 96 + 96 "
                    "sets) 0.3899 vs density 0.3920 -> main. Fixed before any held-out outcome was read.",
    "variants": {
        "main": {"theta_final": {"threshold_g": 3.5, "link_scale_m": 10.0, "survival": 0.3, "b_canopy": -3.0,
                                 "frp_gamma": 0.0, "kernel": "storey"},
                 "lofo_mean_brier": 0.3899, "lofo_mean_auc": 0.529},
        "density": {"theta_final": {"threshold_g": 3.5, "link_scale_m": 10.0, "survival": 0.3, "b_canopy": -3.0,
                                    "frp_gamma": 0.0, "kernel": "storey", "b_dens": 0.5},
                    "lofo_mean_brier": 0.3920, "lofo_mean_auc": 0.563,
                    "status": "secondary, reported, never decides"},
        "sardoy_kernel": {"note": "the Sardoy-located long mode (4.5-10 km) was in the main grid and lost "
                                  "to the Storey-located component on mean Brier; reported via the "
                                  "NO_LONG_MODE / LONG_IGNITION sensitivities, not scored separately"},
    },
    "theta_final": {"threshold_g": 3.5, "link_scale_m": 10.0, "survival": 0.3, "b_canopy": -3.0,
                    "frp_gamma": 0.0, "kernel": "storey"},
    "fixed": "FIXED (b_code 1.9, f_surface 0.007, dry_factor 3) and LONG_IGNITION per kernel (Storey 0.904)",
    "training_cv_disclosed": "Leave-one-fire-out Brier skill vs each fire's base rate negative on every "
                             "fold for both variants: main Eaton -0.14, Palisades -0.19, Camp -3.09, Tubbs "
                             "-3.73, Woolsey -1.14 (AUC 0.56/0.54/0.52/0.51/0.52); density -0.30/-0.10/-2.91/"
                             "-4.54/-1.02 (AUC 0.58/0.60/0.55/0.62/0.47). v4 (fit_v4.json, old Woolsey rule) "
                             "was also negative on every fold. Expectation: the calibration condition fails.",
    "diagnosis_disclosed": "v3 saturates (no survival: Eaton mean P 0.97); wildland ember load ranks loss "
                           "backwards on Palisades/Camp/Tubbs (edge nearness AUC 0.31-0.39); buildings "
                           "within 100 m AUC 0.68-0.73 there, reversed on Woolsey (0.34-0.36).",
    "woolsey_rule": "WOOLSEY_RULE (training only): unsurveyed footprints scored undamaged only within 100 m "
                    "of a DINS-surveyed structure and not within 20 m of an unmatched DINS point (14,539 -> "
                    "4,816 scored). Held-out fires use v4's hybrid rule with DINS 'No Damage' records "
                    "(unsurveyed not scored).",
    "fires": {"glass": "held out (v4 config FIRES4)", "czu": "held out (FIRES4)",
              "northcomplex": "held out (FIRES4)",
              "blinding": "no held-out outcome read by this session; v4's agent was scoring the same "
                          "fires on 2026-10-13 and its results were not opened before this commit; a "
                          "dry run (random outcomes) of Glass and CZU checked the code path"},
    "members": "50 (sensitivities 20); member draws default_rng([20261012, m]); wind draws as v3/v4",
    "metrics": "as PREREG_LR3/LR4: Brier, BSS, log score, AUC, reliability, kappa at 0.5, count-matched, "
               "distance bands, block-level secondary, false alarms in the outside ring",
    "baselines": {"b": "upwind 5 % seeded + Hamada H30", "c": "Chen & McAneney", "d": "count-matched "
                  "distance band", "e": "held-out fire's own base rate", "e2": "training rate 0.6833",
                  "v3_R37": "v3 at its [R37] values on the same units"},
    "decision_rule": "PASS only if, pooled over Glass, CZU and North Complex (250 m blocks within fires, "
                     "2,000 resamples), all four 95 % intervals exclude zero in v5's favour: Brier(c) - "
                     "Brier(v5) > 0; kappa(v5) - kappa(b) > 0; kappa(v5) - kappa(d) > 0; Brier(e) - "
                     "Brier(v5) > 0. Secondary (reported, not deciding): AUC(v5) - 0.5 > 0; Brier(v3) - "
                     "Brier(v5) > 0; the density variant's scores; v4's results from its own harness. "
                     "No configuration may be promoted after scoring.",
    "sensitivities_20_members": "NO_COVARIATES, NO_LONG_MODE, LONG_IGNITION_1KM, NO_URBAN, NO_STOP, "
                                "THRESHOLD_4.6g/24g/91g",
}


def prereg_hash() -> str:
    blob = {"PREREG_LR5": PREREG_LR5, "FIRES4": V4.FIRES4, "GRID": GRID, "FIXED": FIXED}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=float).encode()).hexdigest()


def score(args):
    S3._guard_out(args.out)
    theta = json.loads(args.theta) if args.theta else (PREREG_LR5["variants"][args.variant]["theta_final"]
                                                       if args.variant else PREREG_LR5["theta_final"])
    theta = {**FIXED, **theta}
    g = float(theta.get("frp_gamma", 0.0))
    pooled = {"p": [], "o": [], "blk": [], "base": {}}
    eps = 1.0 / (2 * N_SCORE)
    for fi, fire in enumerate(args.fires):
        cfg = V4.FIRES4.get(fire) or training_cfg(fire)
        n = (args.members or N_SCORE) if not args.quick else 3
        t0 = time.time()
        case = Case5(fire, cfg, dry_run=args.dry_run, n_members=n, gammas=tuple(sorted({0.0, g})), v3_fields=True,
                     kernels=(theta.get("kernel", "sardoy"),))
        p, pe, info = case.run5(theta, n)
        p3 = case.run3(V3_THETA, n)
        sens = {}
        if not args.no_sens:
            ns = min(n, N_SENS)
            sens["NO_COVARIATES"] = case.run5({**theta, "b_canopy": 0.0, "b_code": 0.0}, ns)[0]
            sens["NO_LONG_MODE"] = case.run5(theta, ns, long_mode=False)[0]
            sens["LONG_IGNITION_1KM"] = case.run5({**theta, "long_ignition": LR.V5_LONG_IGNITION_1KM}, ns)[0]
            sens["NO_URBAN"] = case.run5(theta, ns, urban=False)[0]
            sens["NO_STOP"] = case.run5(theta, ns, stop=False)[0]
            for tg in GRID["threshold_g"]:
                if tg != theta["threshold_g"]:
                    sens[f"THRESHOLD_{tg:g}g"] = case.run5({**theta, "threshold_g": tg}, ns)[0]
        sm, o = case.sm, case.obs
        base = V3.baselines(case, int(np.sum(p[sm] >= 0.5)))
        base["e2_training_rate"] = np.full(len(o), TRAINING_RATE)
        base["v3_R37"] = p3
        blk = L1.blocks_of(case.u.x[sm], case.u.y[sm])
        os_ = o[sm]
        kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        res = {"fire": fire, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(), "theta": theta,
               "units_domain": case.n_dom, "units_ring": case.n_ring, "units_scored": int(sm.sum()),
               "observed_destroyed": int(os_.sum()), "wildland_ha": case.wildland_ha, "ffmc": case.cfg["ffmc"],
               "front_contact_units": int(np.isfinite(case.t_front).sum()), "stop_min": case.stop,
               "window": case.window, "viirs_cells": case.viirs_cells, "burn_time_source": case.burn_time_source,
               "covariate_coverage": case.covariates["coverage"],
               "sardoy_extrapolated_share": float(np.mean([m["info"][g]["extrapolated_share"] for m in case.members])),
               "scores": {"V5": V2.prob_scores(p[sm], os_, eps)},
               "sensitivity": {k: V2.prob_scores(v[sm], os_, eps) for k, v in sens.items()},
               "baselines": {k: V2.prob_scores(v[sm], os_, eps) for k, v in base.items()},
               "block_secondary": {"V5": V4.block_scores(case, p), **{k: V4.block_scores(case, v) for k, v in base.items()}},
               "false_alarms_outside": ({"ring_footprints": case.n_ring, "expected_involved": float(p[~case.dom].sum())}
                                        if case.n_ring else None),
               "tests": {"Brier_c_minus_v5": L1.boot_diff(blk, bri, p[sm], base["c_chen_mcaneney"][sm], os_),
                         "kappa_v5_minus_b": L1.boot_diff(blk, kap, p[sm], base["b_windward_edge"][sm], os_),
                         "kappa_v5_minus_d": L1.boot_diff(blk, kap, p[sm], base["d_distance_band"][sm], os_),
                         "Brier_e_minus_v5": L1.boot_diff(blk, bri, p[sm], base["e_climatology"][sm], os_),
                         "Brier_v3_minus_v5": L1.boot_diff(blk, bri, p[sm], p3[sm], os_),
                         "AUC_v5_minus_v3": L1.boot_diff(blk, L1.auc, p[sm], p3[sm], os_),
                         "AUC_v5_minus_half": L1.boot_diff(blk, L1.auc, p[sm], np.full(len(os_), 0.5), os_)}}
        tag = ("_DRYRUN" if args.dry_run else "") + (f"_{args.variant}" if args.variant else "")
        (args.out / f"results_lr5_{fire}{tag}.json").write_text(json.dumps(res, indent=1, default=float))
        np.savez_compressed(args.out / f"units_lr5_{fire}{tag}.npz", x=case.u.x, y=case.u.y, dom=case.dom, scored=sm,
                            obs=o, d_edge=case.d_edge, P_V5=p, **{f"S_{k}": v for k, v in sens.items()},
                            **{f"B_{k}": v for k, v in base.items()})
        pooled["p"].append(p[sm])
        pooled["o"].append(os_)
        pooled["blk"].append(blk + 100000 * fi)
        for k, v in base.items():
            pooled["base"].setdefault(k, []).append(v[sm])
        print(f"{fire}: brier {res['scores']['V5']['brier']:.4f} auc {res['scores']['V5']['auc']:.3f} | "
              f"{(time.time() - t0) / 60:.1f} min rss {L1.rss_gb():.2f} GB", flush=True)
        del case
    if len(args.fires) > 1:
        P = np.concatenate(pooled["p"])
        O = np.concatenate(pooled["o"])
        _, B = np.unique(np.concatenate(pooled["blk"]), return_inverse=True)
        base = {k: np.concatenate(v) for k, v in pooled["base"].items()}
        kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
        bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731
        tests = {"Brier_c_minus_v5": L1.boot_diff(B, bri, P, base["c_chen_mcaneney"], O),
                 "kappa_v5_minus_b": L1.boot_diff(B, kap, P, base["b_windward_edge"], O),
                 "kappa_v5_minus_d": L1.boot_diff(B, kap, P, base["d_distance_band"], O),
                 "Brier_e_minus_v5": L1.boot_diff(B, bri, P, base["e_climatology"], O),
                 "Brier_v3_minus_v5": L1.boot_diff(B, bri, P, base["v3_R37"], O),
                 "AUC_v5_minus_v3": L1.boot_diff(B, L1.auc, P, base["v3_R37"], O),
                 "AUC_v5_minus_half": L1.boot_diff(B, L1.auc, P, np.full(len(O), 0.5), O)}
        dec = {"bss_vs_c_positive_ci": tests["Brier_c_minus_v5"]["p2.5"] > 0,
               "kappa_vs_b_positive_ci": tests["kappa_v5_minus_b"]["p2.5"] > 0,
               "kappa_vs_d_positive_ci": tests["kappa_v5_minus_d"]["p2.5"] > 0,
               "bss_vs_base_rate_positive_ci": tests["Brier_e_minus_v5"]["p2.5"] > 0}
        dec["pass"] = all(dec.values())
        dec["secondary_auc_above_half"] = tests["AUC_v5_minus_half"]["p2.5"] > 0
        dec["secondary_beats_v3_brier"] = tests["Brier_v3_minus_v5"]["p2.5"] > 0
        out = {"fires": args.fires, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(), "theta": theta,
               "units_scored": int(len(O)), "observed_destroyed": int(O.sum()),
               "scores": {"V5": V2.prob_scores(P, O, eps)},
               "baselines": {k: V2.prob_scores(v, O, eps) for k, v in base.items()}, "tests": tests, "decision": dec}
        tag = ("_DRYRUN" if args.dry_run else "") + (f"_{args.variant}" if args.variant else "")
        (args.out / f"results_lr5_pooled_{'_'.join(args.fires)}{tag}.json").write_text(json.dumps(out, indent=1, default=float))
        print(json.dumps({"tests": tests, "decision": dec}, indent=1, default=float))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagnose")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--fires", nargs="+", default=None)
    d.add_argument("--quick", action="store_true")
    a = sub.add_parser("fit")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--quick", action="store_true")
    a.add_argument("--fires", nargs="+", default=None)
    a.add_argument("--kernel", choices=("sardoy", "storey"), default="sardoy")
    a.add_argument("--variant", choices=("main", "density"), default="main")
    a.add_argument("--redo", nargs="+", default=None, help="fires to re-run even if complete")
    e = sub.add_parser("select")
    e.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("lofo")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--fit-dir", type=Path, required=True)
    c.add_argument("--v4-fit", default=str(L1.CALFIRE_DIR / "results-long-range-v4-2026-10-12" / "fit_v4.json"))
    c.add_argument("--fires", nargs="+", default=None)
    c.add_argument("--select-file", default="select_v5.json")
    b = sub.add_parser("score")
    b.add_argument("--fires", nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--quick", action="store_true")
    b.add_argument("--no-sens", action="store_true")
    b.add_argument("--members", type=int, default=None)
    b.add_argument("--theta", default=None)
    b.add_argument("--variant", default=None, help="score a PREREG_LR5 variant (e.g. density)")
    sub.add_parser("prereg")
    args = ap.parse_args()
    {"diagnose": diagnose, "fit": fit, "select": select, "lofo": lofo, "score": score}.get(
        args.cmd, lambda a: (print(json.dumps(PREREG_LR5, indent=1, default=float)), print(prereg_hash())))(args)

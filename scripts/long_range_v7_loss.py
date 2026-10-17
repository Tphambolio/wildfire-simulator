#!/usr/bin/env python3
"""Long-range embers v7, target B: building loss with a combined model on the calibrated front (PREREG_LR7).

Purpose
    v6 target B passed its pre-registered condition (combined model vs density + age, +0.096 AUC) but
    embers added nothing (+0.0001) and the realism reference (AUC 0.70-0.80) was missed (0.60). The v5
    diagnosis showed wildland ember load ranking loss BACKWARDS on the training fires (AUC 0.31-0.39),
    so a single ember coefficient learned a negative sign. v7 changes three things, all fixed before any
    held-out score:
      1. a separate ember-exposure term for buildings at the wildland edge (unit within one 180 m cell
         of a wildland fuel cell, i.e. a G/S/T cell of the v7 fuel grid) and for interior buildings;
      2. a 0-9 m vegetation ring (pre-fire NAIP NDVI canopy share, v7 data agent) beside the 0-30 m ring;
      3. a fire-level loss-rate term (log of the 90th-percentile hourly 10 m wind of the run window,
         observed RAWS; it shifts a fire's intercept, so it changes Brier and reliability but not the
         within-fire AUC);
    and takes fire reach and landed embers from the v7 calibrated-front runs (A0c, V7).

Models (logistic, standardised, L2, C = 1; fitted on the five training fires, LOFO reported)
    B_dens       log(1 + buildings within 100 m)                                         (v6 baseline)
    B_dens_age   B_dens + year built (decades from 1950) + missing flag                  (v6 baseline)
    B_comb_A0c   B_dens_age + log NN separation + canopy 0-9 m + canopy 0-30 m + missing flags
                 + P(reach) from A0c + wildland-edge flag + fire-level wind term
    B_comb_V7    B_comb_A0c with P(reach) from V7 + log10 landed embers x edge + log10 landed x interior

Labels: as v6 (destroyed > 50 % = 1, other surveyed = 0; unsurveyed / Inaccessible not scored; Woolsey
    surveyed-only rule).

Outputs: ``<out>/loss_<tag>.json`` (fire-averaged AUC with 2 km cluster bootstrap, 2,000 draws; Brier;
    reliability; coefficients; ``_about``). Aggregate only: nothing per building leaves this script.

Run: PYTHONPATH=engine/src python3 scripts/long_range_v7_loss.py {lofo,score} --train-dir DIR [--heldout-dir DIR]
     --eta-tag e1e-07_k1 --out DIR --tag TAG [--fires dixie caldor]

References: Knapp et al. (2021) Research Square doi:10.21203/rs.3.rs-580864/v1 p.10; Zamanialaei et al.
    (2025) Nat. Commun. 16:8041 Tables 1-2 p.7; Syphard & Keeley (2019) Fire 2:49; Hakes et al. (2017)
    Fire Technol. 53:2009 (defensible-space distance bands). Status: pre-registered test; illustrative.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import long_range_v6_loss as L6  # noqa: E402
import long_range_v7_arrival as V  # noqa: E402

V4 = L6.V4
BASE = ["log_dens100", "age_dec", "age_missing"]
COMB = BASE + ["log_sep_nn", "canopy_0_9", "canopy_0_9_missing", "canopy_0_30", "canopy_missing", "edge", "fire_wind"]
MODELS = {
    "B_dens": ["log_dens100"],
    "B_dens_age": BASE,
    "B_comb_A0c": COMB + ["p_reach_A0c"],
    "B_comb_V7": COMB + ["p_reach_V7", "log_landed_edge", "log_landed_int"],
}
SEED = V.SEED


def canopy_0_9(fire, ids):
    out = np.full(len(ids), np.nan)
    pos = {int(u): k for k, u in enumerate(ids)}
    sets = [("woolsey_fp", 0), ("woolsey", 10_000_000)] if fire == "woolsey" else [(fire, 0)]
    for stem, off in sets:
        for p in (V4 / f"{stem}_unit_canopy_0_9.csv", V4 / f"{stem}_unit_canopy.csv"):
            if not p.exists():
                continue
            rows = L6._read(p)
            if not rows or "canopy_0_9" not in rows[0]:
                continue
            for r in rows:
                k = pos.get(int(float(r["unit_id"])) + off)
                if k is not None and np.isnan(out[k]):
                    out[k] = L6._num(r.get("canopy_0_9"))
            break
    if np.nanmax(np.r_[out, [-1]]) > 1.5:
        out = out / 100.0
    return out


def fire_wind(fire: str) -> float:
    """log of the 90th percentile hourly 10 m wind (km/h, observed station x 1.15) in the run window."""
    cfg = V.cfg_v7(fire)
    wx = V.weather(fire, cfg)
    return float(math.log(np.percentile(wx["mph"] * 1.609 * V.RAW_TO_10M, 90)))


def exposure(fire, lng, lat, run_dir: Path, eta_tag: str, members=None):
    from scipy import ndimage

    cfg = V.cfg_v7(fire)
    g = V.load_grid_v7(fire)
    dur = cfg["duration"]
    r = np.clip(((g["lat_max"] - lat) / g["cell_lat"]).astype(int), 0, g["rows"] - 1)
    c = np.clip(((lng - g["lng_min"]) / g["cell_lng"]).astype(int), 0, g["cols"] - 1)
    wild = np.isin(g["group"], ["G", "S", "T"])
    near_wild = ndimage.maximum_filter(wild.astype(np.uint8), size=3) > 0
    out = {"edge": near_wild[r, c].astype(float)}
    for model, tag in (("A0c", "A0c"), ("V7", f"V7_{eta_tag}")):
        mem = V.load_runs(run_dir / fire, tag, members)
        if not mem:
            out[f"p_reach_{model}"] = np.full(len(lat), np.nan)
            continue
        hit = np.zeros(len(lat))
        landed = np.zeros((g["rows"], g["cols"]))
        for m in mem:
            reached = np.isfinite(m["arrival"]) & (m["arrival"] <= dur)
            reached = ndimage.maximum_filter(reached.astype(np.uint8), size=3) > 0
            hit += reached[r, c]
            if model == "V7" and m.get("landed") is not None and np.ndim(m["landed"]) == 2:
                landed += m["landed"]
        out[f"p_reach_{model}"] = hit / len(mem)
        if model == "V7":
            ll = np.log10(1.0 + landed[r, c] / len(mem))
            out["log_landed_edge"] = ll * out["edge"]
            out["log_landed_int"] = ll * (1.0 - out["edge"])
    return out


def features(fire, run_dir, eta_tag, members=None, woolsey_rule="surveyed"):
    from scipy.spatial import cKDTree

    ids, lng, lat, st, yb = L6.load_units(fire, woolsey_rule)
    mlng = 111_320.0 * math.cos(math.radians(float(np.mean(lat))))
    xy = np.column_stack([lng * mlng, lat * 111_320.0])
    tree = cKDTree(xy)
    dens = np.array([len(v) - 1 for v in tree.query_ball_point(xy, 100.0)])
    dnn = tree.query(xy, k=2)[0][:, 1]
    ybn = np.array([L6._num(v) for v in yb])
    ybn = np.where((ybn > 1800) & (ybn <= 2025), ybn, np.nan)
    can = L6.canopy(fire, ids)
    c9 = canopy_0_9(fire, ids)
    X = {"log_dens100": np.log1p(dens), "log_sep_nn": np.log(np.maximum(dnn, 1.0)),
         "age_dec": np.where(np.isfinite(ybn), (ybn - 1950.0) / 10.0, 0.0), "age_missing": (~np.isfinite(ybn)).astype(float),
         "canopy_0_30": np.where(np.isfinite(can), can, 0.0), "canopy_missing": (~np.isfinite(can)).astype(float),
         "canopy_0_9": np.where(np.isfinite(c9), c9, 0.0), "canopy_0_9_missing": (~np.isfinite(c9)).astype(float),
         "fire_wind": np.full(len(ids), fire_wind(fire))}
    X.update(exposure(fire, lng, lat, run_dir, eta_tag, members))
    y = (st == "Destroyed (>50%)").astype(float)
    scored = ~np.isin(st, ["UNSURVEYED", "Inaccessible"])
    blocks = (np.floor(xy[:, 0] / 2000.0).astype(np.int64) * 100000 + np.floor(xy[:, 1] / 2000.0).astype(np.int64))
    cov = {"year_built": float(np.mean(np.isfinite(ybn[scored]))) if scored.any() else None,
           "canopy_0_30": float(np.mean(np.isfinite(can[scored]))) if scored.any() else None,
           "canopy_0_9": float(np.mean(np.isfinite(c9[scored]))) if scored.any() else None,
           "edge_share": float(np.mean(X["edge"][scored])) if scored.any() else None}
    return {k: v[scored] for k, v in X.items()}, y[scored], blocks[scored], cov


def summarise(per_fire, train_rate):
    out = {"per_fire": {}, "fire_averaged": {}}
    for d in per_fire:
        out["per_fire"][d["fire"]] = {"units": int(len(d["y"])), "coverage": d["cov"], "models": {}}
        for k, p in d["P"].items():
            out["per_fire"][d["fire"]]["models"][k] = {
                "auc": L6.auc(p, d["y"]), "brier": float(np.mean((p - d["y"]) ** 2)),
                "brier_base": float(np.mean((train_rate - d["y"]) ** 2)), "reliability": L6.reliability(p, d["y"]),
                "coef": d["coef"].get(k)}
    for k in MODELS:
        out["fire_averaged"][k] = L6.boot_auc(per_fire, k)
    out["tests"] = {f"{b}_minus_{a}": L6.boot_auc_diff(per_fire, a, b) for a, b in
                    (("B_dens_age", "B_comb_V7"), ("B_comb_A0c", "B_comb_V7"), ("B_dens_age", "B_comb_A0c"),
                     ("B_dens", "B_dens_age"))}
    out["brier_fire_averaged"] = {k: float(np.mean([np.mean((d["P"][k] - d["y"]) ** 2) for d in per_fire])) for k in MODELS}
    return out


def _fit_all(train, test):
    P, coef = {}, {}
    for k, cols in MODELS.items():
        P[k], coef[k] = L6.fit_predict(train, test, cols)
    return P, coef


def cmd_lofo(args):
    data = {f: features(f, args.train_dir, args.eta_tag, args.members) for f in V.TRAINING}
    per_fire = []
    for f in V.TRAINING:
        P, coef = _fit_all([data[g] for g in V.TRAINING if g != f], data[f])
        per_fire.append({"fire": f, "y": data[f][1], "blocks": data[f][2], "P": P, "coef": coef, "cov": data[f][3]})
    rate = float(np.mean(np.concatenate([data[f][1] for f in V.TRAINING])))
    out = {"_about": {"purpose": "v7 target B leave-one-fire-out CV on the training fires", "units": "AUC, Brier",
                      "eta_tag": args.eta_tag, "status": "training (exploratory fit)", "models": MODELS,
                      "script": "scripts/long_range_v7_loss.py lofo"},
           **summarise(per_fire, rate), "training_rate": rate}
    (args.out / f"loss_lofo_{args.tag}.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out["fire_averaged"], indent=1), json.dumps(out["tests"], indent=1))


def cmd_score(args):
    train = [features(f, args.train_dir, args.eta_tag, args.members) for f in V.TRAINING]
    rate = float(np.mean(np.concatenate([d[1] for d in train])))
    per_fire = []
    for f in args.fires:
        d = features(f, args.heldout_dir, args.eta_tag, args.members)
        P, coef = _fit_all(train, d)
        per_fire.append({"fire": f, "y": d[1], "blocks": d[2], "P": P, "coef": coef, "cov": d[3]})
    out = {"_about": {"purpose": f"PREREG_LR7 target B held-out scores ({args.tag})", "units": "AUC, Brier",
                      "eta_tag": args.eta_tag, "status": args.tag, "prereg_sha256": V.prereg_hash(), "prereg_addenda_sha256": V.addendum_hash(), "models": MODELS,
                      "script": "scripts/long_range_v7_loss.py score"},
           **summarise(per_fire, rate), "training_rate": rate}
    (args.out / f"loss_{args.tag}.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out["fire_averaged"], indent=1), json.dumps(out["tests"], indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lofo", "score"):
        a = sub.add_parser(name)
        a.add_argument("--train-dir", type=Path, required=True)
        a.add_argument("--heldout-dir", type=Path, default=None)
        a.add_argument("--eta-tag", required=True)
        a.add_argument("--members", type=int, default=None)
        a.add_argument("--out", type=Path, required=True)
        a.add_argument("--tag", default="exploratory")
        a.add_argument("--fires", nargs="+", default=list(V.SECONDARY))
    args = ap.parse_args()
    {"lofo": cmd_lofo, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()

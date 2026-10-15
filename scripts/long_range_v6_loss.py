#!/usr/bin/env python3
"""Long-range embers v6, target B: building loss with a combined model (spec §6.8, PREREG_LR6).

Purpose
    The owner's decision of 2026-10-13 scores building loss separately from ember transport.
    Ember arrival starts the loss; house-to-house spread, governed by building density, building
    age/code and near-building vegetation, then decides which buildings are lost. This script fits
    and scores, per CAL FIRE DINS building unit (aggregate scores only; nothing per building leaves
    this script):
      - B_dens       density-only logistic (log buildings within 100 m; Knapp et al. 2021);
      - B_dens_age   density + year built (decades from 1950, missing flag);
      - B_comb_A0    combined: + fire exposure from target A's no-long-range-ember runs (share of
                     members in which fire reached the unit's cell) + canopy 0-30 m + nearest-
                     neighbour separation;
      - B_comb_V6    combined with target A's V6 runs: + long-range landed embers per m² (log10).
    Logistic regression on standardised features (scikit-learn, L2, C = 1), fitted leave-one-fire-out
    on the training fires (Eaton, Palisades, Camp, Tubbs, Woolsey) and once on all five for the
    held-out fires (Glass, CZU, North Complex).

Inputs
    calfire-dins ``derived/<fire>_units*.gpkg`` and ``*_units_status.csv`` (CAL FIRE DINS, CC BY;
    the status files of held-out fires are sealed until PREREG_LR6 is pushed), ``derived/v4/*_unit_canopy.csv``
    (NAIP / Meta CHM canopy rings, v4 data agent), DINS ``YEARBUILT`` carried in the unit files, and
    target-A ensemble runs (``long_range_v6_arrival.py``).

Labels
    Destroyed (> 50 %) = 1; every other surveyed class = 0; unsurveyed and Inaccessible are not
    scored. **Woolsey fix (v6):** Woolsey DINS records only damaged structures, so v3-v5 scored
    unsurveyed footprints as undamaged [H]; v6 scores only surveyed Woolsey units (the label there
    becomes destroyed vs damaged-not-destroyed) -- documented in PREREG_LR6 and the report.

Outputs: ``<out>/loss_<tag>.json`` (AUC, Brier, reliability bins, bootstrap CIs; ``_about`` block).

Run: PYTHONPATH=engine/src python3 scripts/long_range_v6_loss.py {diagnose,lofo,score} --arrival-dir DIR --eta E --out DIR

References: Knapp et al. (2021) Research Square preprint doi:10.21203/rs.3.rs-580864/v1 p.10;
    Zamanialaei et al. (2025) Nat. Commun. 16:8041 Tables 1-2 p.7 (AUC 0.64-0.84 per fire);
    Syphard & Keeley (2019) Fire 2:49. Status: pre-registered test (PREREG_LR6); illustrative.
"""

from __future__ import annotations

import argparse
import csv
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

import long_range_v6_arrival as A  # noqa: E402

CALFIRE = A.CALFIRE
V4 = CALFIRE / "derived" / "v4"
MODELS = {
    "B_dens": ["log_dens100"],
    "B_dens_age": ["log_dens100", "age_dec", "age_missing"],
    "B_comb_A0": ["log_dens100", "age_dec", "age_missing", "log_sep_nn", "canopy_0_30", "canopy_missing", "p_reach_A0"],
    "B_comb_V6": ["log_dens100", "age_dec", "age_missing", "log_sep_nn", "canopy_0_30", "canopy_missing", "p_reach_V6",
                  "log_landed_V6"],
}
SEED = 20261013


def _read(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def load_units(fire: str, woolsey_rule: str = "surveyed"):
    """(ids, lng, lat, status, year_built) for a fire's unit set (unit rules of v3/v4)."""
    import geopandas as gpd

    if fire == "woolsey":
        fp = gpd.read_file(CALFIRE / "derived" / "woolsey_fp_units.gpkg")
        fp = fp[fp.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        pts = gpd.read_file(CALFIRE / "derived" / "woolsey_units.gpkg")
        matched = set(int(x) for x in fp["OBJECTID"].dropna()) if "OBJECTID" in fp else set()
        extra = pts[~pts.unit_id.astype(int).isin(matched)]
        c1 = fp.geometry.representative_point()
        ids = np.concatenate([fp.unit_id.to_numpy(np.int64), 10_000_000 + extra.unit_id.to_numpy(np.int64)])
        lng = np.concatenate([c1.x.to_numpy(), extra.geometry.x.to_numpy()])
        lat = np.concatenate([c1.y.to_numpy(), extra.geometry.y.to_numpy()])
        yb = np.concatenate([fp.YEARBUILT.to_numpy(), extra.YEARBUILT.to_numpy()])
        s1 = {int(r["unit_id"]): r["DAMAGE"] for r in _read(CALFIRE / "derived" / "woolsey_fp_units_status.csv")}
        s2 = {10_000_000 + int(r["unit_id"]): r["DAMAGE"] for r in _read(CALFIRE / "derived" / "woolsey_units_status.csv")}
        st = np.array([s1.get(int(i), s2.get(int(i), "UNSURVEYED")) for i in ids])
        if woolsey_rule == "old":  # v3-v5: unsurveyed footprints = undamaged [H]
            st = np.where(st == "UNSURVEYED", "No Damage (assumed)", st)
        return ids, lng, lat, st, yb
    g = gpd.read_file(CALFIRE / "derived" / f"{fire}_units.gpkg")
    if not (g.geometry.geom_type == "Point").all():
        g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    c = g.geometry.representative_point()
    ids, lng, lat, yb = g.unit_id.to_numpy(np.int64), c.x.to_numpy(), c.y.to_numpy(), g.YEARBUILT.to_numpy()
    hyb = CALFIRE / "derived" / f"{fire}_units_unmatched_dins.gpkg"
    if hyb.exists():
        p = gpd.read_file(hyb)
        ids = np.concatenate([ids, p.unit_id.to_numpy(np.int64)])
        lng = np.concatenate([lng, p.geometry.x.to_numpy()])
        lat = np.concatenate([lat, p.geometry.y.to_numpy()])
        yb = np.concatenate([yb, p.YEARBUILT.to_numpy() if "YEARBUILT" in p else np.full(len(p), np.nan)])
    sd = {int(float(r["unit_id"])): r["DAMAGE"] for r in _read(CALFIRE / "derived" / f"{fire}_units_status.csv")}
    st = np.array([sd.get(int(i), "UNSURVEYED") for i in ids])
    return ids, lng, lat, st, yb


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else np.nan
    except (TypeError, ValueError):
        return np.nan


def canopy(fire, ids):
    out = np.full(len(ids), np.nan)
    pos = {int(u): k for k, u in enumerate(ids)}
    sets = [("woolsey_fp", 0), ("woolsey", 10_000_000)] if fire == "woolsey" else [(fire, 0)]
    for stem, off in sets:
        p = V4 / f"{stem}_unit_canopy.csv"
        if not p.exists():
            continue
        for r in _read(p):
            k = pos.get(int(float(r["unit_id"])) + off)
            if k is not None:
                out[k] = _num(r.get("canopy_0_30"))
    if np.nanmax(np.r_[out, [-1]]) > 1.5:
        out = out / 100.0
    return out


def exposure(fire, lng, lat, arrival_dir: Path, eta: float, members=None):
    """Per unit: share of members with fire in the unit's cell or its 8 neighbours by the run end
    (A0, V6), and log10(1 + member-mean landed embers per m²) (V6)."""
    g = A.load_grid(fire)
    dur = A.fire_cfg(fire)["duration"]
    r = np.clip(((g["lat_max"] - lat) / g["cell_lat"]).astype(int), 0, g["rows"] - 1)
    c = np.clip(((lng - g["lng_min"]) / g["cell_lng"]).astype(int), 0, g["cols"] - 1)
    out = {}
    from scipy import ndimage

    for model, e in (("A0", 0.0), ("V6", eta)):
        mem = A.load_members(arrival_dir / fire, model, e, members)
        if not mem:
            out[f"p_reach_{model}"] = np.full(len(lat), np.nan)
            continue
        hit = np.zeros(len(lat))
        landed = np.zeros((g["rows"], g["cols"]))
        for m in mem:
            reached = np.isfinite(m["arrival"]) & (m["arrival"] <= dur)
            reached = ndimage.maximum_filter(reached.astype(np.uint8), size=3) > 0
            hit += reached[r, c]
            if model == "V6" and m.get("landed") is not None and np.ndim(m["landed"]) == 2:
                landed += m["landed"]
        out[f"p_reach_{model}"] = hit / len(mem)
        if model == "V6":
            out["log_landed_V6"] = np.log10(1.0 + landed[r, c] / len(mem))
    return out


def features(fire, arrival_dir, eta, members=None, woolsey_rule="surveyed"):
    from scipy.spatial import cKDTree

    ids, lng, lat, st, yb = load_units(fire, woolsey_rule)
    mlng = 111_320.0 * math.cos(math.radians(float(np.mean(lat))))
    xy = np.column_stack([lng * mlng, lat * 111_320.0])
    tree = cKDTree(xy)
    dens = np.array([len(v) - 1 for v in tree.query_ball_point(xy, 100.0)])
    dnn = tree.query(xy, k=2)[0][:, 1]
    ybn = np.array([_num(v) for v in yb])
    ybn = np.where((ybn > 1800) & (ybn <= 2025), ybn, np.nan)
    can = canopy(fire, ids)
    X = {"log_dens100": np.log1p(dens), "log_sep_nn": np.log(np.maximum(dnn, 1.0)),
         "age_dec": np.where(np.isfinite(ybn), (ybn - 1950.0) / 10.0, 0.0), "age_missing": (~np.isfinite(ybn)).astype(float),
         "canopy_0_30": np.where(np.isfinite(can), can, 0.0), "canopy_missing": (~np.isfinite(can)).astype(float)}
    X.update(exposure(fire, lng, lat, arrival_dir, eta, members))
    y = (st == "Destroyed (>50%)").astype(float)
    scored = ~np.isin(st, ["UNSURVEYED", "Inaccessible"])
    blocks = (np.floor(xy[:, 0] / 2000.0).astype(np.int64) * 100000 + np.floor(xy[:, 1] / 2000.0).astype(np.int64))
    cov = {"year_built": float(np.mean(np.isfinite(ybn[scored]))) if scored.any() else None,
           "canopy": float(np.mean(np.isfinite(can[scored]))) if scored.any() else None}
    return {k: v[scored] for k, v in X.items()}, y[scored], blocks[scored], cov


def fit_predict(train, test, cols):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    Xtr = np.column_stack([np.concatenate([d[0][c] for d in train]) for c in cols])
    ytr = np.concatenate([d[1] for d in train])
    sc = StandardScaler().fit(Xtr)
    m = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(Xtr), ytr)
    Xte = np.column_stack([test[0][c] for c in cols])
    coef = dict(zip(cols, (m.coef_[0] / sc.scale_).round(4).tolist()))
    return m.predict_proba(sc.transform(Xte))[:, 1], coef


def auc(p, y):
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan")


def reliability(p, y, bins=10):
    e = np.linspace(0, 1, bins + 1)
    k = np.clip(np.digitize(p, e) - 1, 0, bins - 1)
    return [{"bin": f"{e[i]:.1f}-{e[i + 1]:.1f}", "n": int((k == i).sum()),
             "mean_p": float(p[k == i].mean()) if (k == i).any() else None,
             "observed": float(y[k == i].mean()) if (k == i).any() else None} for i in range(bins)]


def boot_auc_diff(per_fire, a, b, n=2000, seed=SEED):
    """Fire-averaged AUC(b) - AUC(a); 2 km cluster bootstrap within each fire (percentile CI)."""
    rng = np.random.default_rng(seed)
    stats = []
    prep = []
    for d in per_fire:
        u, inv = np.unique(d["blocks"], return_inverse=True)
        idx = [np.nonzero(inv == j)[0] for j in range(len(u))]
        prep.append((d, idx))
    for _ in range(n):
        diffs = []
        for d, idx in prep:
            s = np.concatenate([idx[j] for j in rng.integers(0, len(idx), len(idx))])
            diffs.append(auc(d["P"][b][s], d["y"][s]) - auc(d["P"][a][s], d["y"][s]))
        stats.append(np.nanmean(diffs))
    point = float(np.nanmean([auc(d["P"][b], d["y"]) - auc(d["P"][a], d["y"]) for d in per_fire]))
    return {"diff": point, "p2.5": float(np.nanpercentile(stats, 2.5)), "p97.5": float(np.nanpercentile(stats, 97.5))}


def boot_auc(per_fire, a, n=2000, seed=SEED):
    rng = np.random.default_rng(seed + 1)
    stats = []
    prep = []
    for d in per_fire:
        u, inv = np.unique(d["blocks"], return_inverse=True)
        prep.append((d, [np.nonzero(inv == j)[0] for j in range(len(u))]))
    for _ in range(n):
        v = []
        for d, idx in prep:
            s = np.concatenate([idx[j] for j in rng.integers(0, len(idx), len(idx))])
            v.append(auc(d["P"][a][s], d["y"][s]))
        stats.append(np.nanmean(v))
    return {"auc": float(np.nanmean([auc(d["P"][a], d["y"]) for d in per_fire])),
            "p2.5": float(np.nanpercentile(stats, 2.5)), "p97.5": float(np.nanpercentile(stats, 97.5))}


def summarise(per_fire, train_rate):
    out = {"per_fire": {}, "fire_averaged": {}}
    for d in per_fire:
        out["per_fire"][d["fire"]] = {"units": int(len(d["y"])), "destroyed_rate": float(d["y"].mean()),
                                      "coverage": d["cov"], "models": {}}
        for k, p in d["P"].items():
            out["per_fire"][d["fire"]]["models"][k] = {
                "auc": auc(p, d["y"]), "brier": float(np.mean((p - d["y"]) ** 2)),
                "brier_base": float(np.mean((train_rate - d["y"]) ** 2)), "reliability": reliability(p, d["y"]),
                "coef": d["coef"].get(k)}
    for k in MODELS:
        out["fire_averaged"][k] = boot_auc(per_fire, k)
    out["tests"] = {f"{b}_minus_{a}": boot_auc_diff(per_fire, a, b) for a, b in
                    (("B_dens_age", "B_comb_V6"), ("B_dens_age", "B_comb_A0"), ("B_comb_A0", "B_comb_V6"),
                     ("B_dens", "B_dens_age"))}
    return out


def cmd_lofo(args):
    data = {f: features(f, args.arrival_dir, args.eta, args.members, args.woolsey_rule) for f in A.TRAINING}
    per_fire = []
    for f in A.TRAINING:
        tr = [data[g] for g in A.TRAINING if g != f]
        P, coef = {}, {}
        for k, cols in MODELS.items():
            P[k], coef[k] = fit_predict(tr, data[f], cols)
        per_fire.append({"fire": f, "y": data[f][1], "blocks": data[f][2], "P": P, "coef": coef, "cov": data[f][3]})
    rate = float(np.mean(np.concatenate([data[f][1] for f in A.TRAINING])))
    out = {"_about": {"purpose": "v6 target B leave-one-fire-out CV on training fires", "units": "AUC, Brier",
                      "woolsey_rule": args.woolsey_rule, "eta": args.eta, "status": "training (exploratory fit)"},
           **summarise(per_fire, rate), "training_rate": rate}
    (args.out / f"loss_lofo_{args.tag}.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: v for k, v in out["fire_averaged"].items()}, indent=1),
          json.dumps({f: {k: round(v["auc"], 3) for k, v in d["models"].items()} for f, d in out["per_fire"].items()}, indent=1),
          json.dumps(out["tests"], indent=1))


def cmd_score(args):
    train = [features(f, args.arrival_dir, args.eta, args.members, "surveyed") for f in A.TRAINING]
    rate = float(np.mean(np.concatenate([d[1] for d in train])))
    per_fire = []
    for f in args.fires:
        d = features(f, args.heldout_dir, args.eta, args.members)
        P, coef = {}, {}
        for k, cols in MODELS.items():
            P[k], coef[k] = fit_predict(train, d, cols)
        per_fire.append({"fire": f, "y": d[1], "blocks": d[2], "P": P, "coef": coef, "cov": d[3]})
    out = {"_about": {"purpose": "PREREG_LR6 target B held-out scores", "units": "AUC, Brier", "eta": args.eta,
                      "status": args.tag, "prereg_sha256": A.prereg_hash()},
           **summarise(per_fire, rate), "training_rate": rate}
    (args.out / f"loss_{args.tag}.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out["fire_averaged"], indent=1), json.dumps(out["tests"], indent=1))


def cmd_diagnose(args):
    """Woolsey label check: AUC of density and year built under the old and the surveyed-only rule."""
    res = {}
    for rule in ("old", "surveyed"):
        X, y, b, cov = features("woolsey", args.arrival_dir, args.eta, args.members, rule)
        res[rule] = {"units": int(len(y)), "destroyed_rate": float(y.mean()),
                     "auc_density100": auc(X["log_dens100"], y),
                     "auc_minus_year_built": auc(np.where(X["age_missing"] > 0, np.nan, -X["age_dec"])[X["age_missing"] == 0],
                                                 y[X["age_missing"] == 0]),
                     "auc_p_reach_A0": auc(X["p_reach_A0"], y)}
    (args.out / "woolsey_label_check.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lofo", "score", "diagnose"):
        a = sub.add_parser(name)
        a.add_argument("--arrival-dir", type=Path, required=True)
        a.add_argument("--heldout-dir", type=Path, default=None)
        a.add_argument("--eta", type=float, required=True)
        a.add_argument("--members", type=int, default=None)
        a.add_argument("--out", type=Path, required=True)
        a.add_argument("--tag", default="exploratory")
        a.add_argument("--woolsey-rule", default="surveyed")
        a.add_argument("--fires", nargs="+", default=list(A.HELDOUT))
    args = ap.parse_args()
    {"lofo": cmd_lofo, "score": cmd_score, "diagnose": cmd_diagnose}[args.cmd](args)


if __name__ == "__main__":
    main()

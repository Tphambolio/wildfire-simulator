"""Attribution tables, stratifications and figures for the US transfer diagnosis ([R71]).

Purpose: turn the paired sensitivity runs of ``sens_run.py`` into
  1. the baseline over-prediction on the development fires (US and Canada through the same pipeline), next to
     a *description* of the spent R67 sealed confirm fires (their ``det_C1`` deterministic run; nothing is
     re-run or tuned on them);
  2. per-variant paired effects: Delta = L_variant - L_base per fire-day, where
     L = log10(modelled 17 h growth + 10 ha) - log10(observed 17 h growth + 10 ha) (the R67/B2 area metric,
     deterministic member), with 95 % percentile bootstrap CIs resampling whole fires (2,000 draws, seed 1);
  3. cause (c) (suppression/burnout) by stratification: fire stage (share of the final footprint burned before
     the day), days after WFIGS containment, observed static share of the start perimeter;
  4. cause (e) (scoring geometry) from the base record's extras (growth inside the final footprint, on cells
     burned on later days, detected-only observed growth) and the FEDS-DOB variant;
  5. the over-prediction by FBFM40 group (where the modelled growth lands).
Outputs go to ``<out>/``: ``results.json`` (every number in the report), ``per_fireday.csv``, ``fig*.png``.

Usage: ``PYTHONPATH=engine/src python3 -m scripts.validation.us_transfer.analyse <out_dir>``
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

from . import common as C

SEALED_DET = C.W / "validation-data/sealed2026-confirm/runs/det_C1.jsonl"
NBOOT, SEED = 2000, 1
LABELS = {
    "act1": "(d) last 1 burn day active", "perim": "(d) whole start perimeter active",
    "stn": "(b) station T/RH/wind + station codes", "stn_wind": "(b) station wind only",
    "stn_trh": "(b) station T/RH only", "stn_fwi": "(b) station daily codes only",
    "nospin": "(b) no hourly FFMC spin-up", "wind075": "(b) wind x 0.75 (elasticity)",
    "cure95": "(a) O-1 curing 95 %", "cure_ndvi": "(a) O-1 curing from NDVI",
    "xw_sh_o1b": "(a) shrub -> O-1b", "xw_sh_m1": "(a) shrub -> M-1/M-2 50 %",
    "xw_sh_d1": "(a) shrub -> D-1/D-2", "xw_sh_c1": "(a) shrub -> C-1",
    "xw_gs_c4": "(a) grass-shrub -> C-4", "xw_gr_o1a": "(a) grass -> O-1a",
    "xw_t_c5": "(a) timber -> C-5", "xw_tl_d1": "(a) timber litter -> D-1/D-2",
    "xw_nb1_nf": "(a) urban -> non-fuel", "feds": "(e) DOB from FEDS (R67 source)",
    "rm050": "(c) ROS x 0.5 (damping dose)", "rm030": "(c) ROS x 0.3 (damping dose)",
    "rm020": "(c) ROS x 0.2 (damping dose)", "rm010": "(c) ROS x 0.1 (damping dose)",
}
SHARE_BINS = ((0.0, 0.02), (0.02, 0.10), (0.10, 0.30), (0.30, 1.01))
STAGE_BINS = ((0, .25, "stage<0.25"), (.25, .6, "0.25-0.6"), (.6, 1.01, "stage>=0.6"))


def L(pred, obs):
    return math.log10(pred + 10.0) - math.log10(obs + 10.0)


def lgc(g):
    return math.log10(min(max(g, 0.05), 20.0)) if g == g else math.nan


def boot(vals, fires, stat=np.mean, n=NBOOT, seed=SEED):
    """Point and 95 % percentile CI, resampling whole fires."""
    vals, fires = np.asarray(vals, float), np.asarray(fires)
    ok = np.isfinite(vals)
    vals, fires = vals[ok], fires[ok]
    if len(vals) == 0:
        return [math.nan, math.nan, math.nan, 0, 0]
    uf = np.unique(fires)
    idx = {f: np.nonzero(fires == f)[0] for f in uf}
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(n):
        pick = rng.choice(uf, len(uf))
        ii = np.concatenate([idx[f] for f in pick])
        bs.append(stat(vals[ii]))
    return [float(stat(vals)), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)),
            int(len(vals)), int(len(uf))]


def read(v):
    p = C.DATA / "runs" / f"{v}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        r = json.loads(line)
        out[(r["fire_id"], r["day"])] = r
    return out


def stage_info(fid, day, cache={}):
    from firesim.validation.cfsds import FireDomain

    if fid not in cache:
        cache.clear()
        cache[fid] = FireDomain.load(C.DATA / "domains" / f"{fid}.npz")
    d = cache[fid]
    tot = (d.dob > 0).sum()
    return float(((d.dob > 0) & (d.dob < day)).sum() / tot)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    man = {m["fire_id"]: m for m in json.loads((C.DATA / "manifest.json").read_text())}
    info = {f: json.loads((C.DATA / "inputs" / f"{f}.json").read_text()) for f in man}
    base = {k: r for k, r in read("base").items() if "error" not in r}
    res = {"_about": {"purpose": "every number of the R71 report", "metric":
                      "L = log10(pred 17 h growth + 10 ha) - log10(obs + 10 ha); Delta = L_variant - L_base",
                      "ci": "95 % percentile bootstrap over whole fires, 2000 draws, seed 1",
                      "created_by": "scripts/validation/us_transfer/analyse.py"}}
    rows = []
    for (f, d), r in base.items():
        h = r["17h"]
        rows.append({"fire_id": f, "day": d, "region": man[f]["region"], "year": man[f]["year"],
                     "L": L(h["pred_ha"], h["obs_ha"]), "lg": lgc(h["growth_ratio"]), "g": h["growth_ratio"],
                     "f1": h["f1"] if h["f1"] == h["f1"] else 0.0, "obs_ha": h["obs_ha"], "pred_ha": h["pred_ha"],
                     "start_type": r.get("start_type"), "run_s": r["run_s"], "ws": r["mean_ws_17"],
                     "maxws": r["max_ws_17"], "minrh": r["min_rh_17"], "ffmc": r["ffmc"], "bui": r["bui"],
                     "res_m": man[f]["res_m"], "initial_ha": r["initial_ha"],
                     "static_obs": (r.get("start_perimeter_obs") or {}).get("static_share"),
                     "static_model": h.get("static_share_model"),
                     "L_clip": L(h.get("pred_in_final_ha", math.nan), h["obs_ha"]),
                     "share_outside_final": 1 - h.get("pred_in_final_ha", 0) / h["pred_ha"] if h["pred_ha"] > 0 else math.nan,
                     "share_later": h.get("pred_later_ha", 0) / h["pred_ha"] if h["pred_ha"] > 0 else math.nan,
                     "L_det": L(h["pred_ha"], h["det_obs_ha"]) if "det_obs_ha" in h else math.nan,
                     "f1_det": h.get("det_f1", math.nan), "edge_hit": r.get("edge_hit"),
                     "stage": stage_info(f, d), "prev_share": r.get("start_prev_day_share"),
                     "start_age_days": r.get("start_age_days")})
    # WFIGS containment (US)
    for row in rows:
        w = info[row["fire_id"]].get("wfigs") or {}
        cont = w.get("attr_ContainmentDateTime")
        disc = w.get("attr_FireDiscoveryDateTime")
        from datetime import date, datetime
        dday = date(row["year"], 1, 1).toordinal() + row["day"] - 1
        row["days_after_containment"] = (dday - datetime.fromisoformat(cont).date().toordinal()) if cont else math.nan
        row["days_since_discovery"] = (dday - datetime.fromisoformat(disc).date().toordinal()) if disc else \
            (dday - date.fromisoformat(man[row["fire_id"]]["t0"]).toordinal())
    by = {}
    for reg in ("US", "CAN"):
        rr = [r for r in rows if r["region"] == reg]
        if not rr:
            continue
        fz = [r["fire_id"] for r in rr]
        by[reg] = {
            "mean_L": boot([r["L"] for r in rr], fz),
            "mean_log10_gc": boot([r["lg"] for r in rr], fz),
            "median_g": boot([r["g"] for r in rr], fz, stat=np.nanmedian),
            "share_g_ge_20": boot([float(r["g"] >= 20) if r["g"] == r["g"] else math.nan for r in rr], fz),
            "mean_f1": boot([r["f1"] for r in rr], fz),
            "mean_L_mature": boot([r["L"] for r in rr if r["start_type"] == "mature"],
                                  [r["fire_id"] for r in rr if r["start_type"] == "mature"]),
            "median_obs_ha": float(np.median([r["obs_ha"] for r in rr])),
            "median_ws": float(np.median([r["ws"] for r in rr])), "median_ffmc": float(np.median([r["ffmc"] for r in rr])),
            "median_bui": float(np.median([r["bui"] for r in rr])),
            "edge_hit_share": float(np.mean([bool(r["edge_hit"]) for r in rr])),
        }
    # sealed R67 det_C1 (described only)
    if SEALED_DET.exists():
        sr = [json.loads(x) for x in SEALED_DET.read_text().splitlines()]
        sr = [r for r in sr if "error" not in r]
        sv, sf, sg = [], [], []
        for r in sr:
            h = r["members"]["det"]["17h"]
            sv.append(L(h["area_pred"] / 1e4, h["area_obs"] / 1e4)); sf.append(r["fire_id"]); sg.append(h["growth_ratio"])
        by["SEALED_R67_described"] = {"mean_L": boot(sv, sf), "median_g": float(np.nanmedian(sg)),
                                      "share_g_ge_20": float(np.nanmean(np.array(sg, float) >= 20)),
                                      "median_obs_ha": float(np.median([r["obs_growth_ha"] for r in sr])),
                                      "median_ws": float(np.median([r["mean_ws_kmh"] for r in sr])),
                                      "median_ffmc": float(np.median([r["ffmc"] for r in sr])),
                                      "median_bui": float(np.median([r["bui"] for r in sr])),
                                      "note": "det_C1 of the spent R67 confirm fires; described, never tuned on"}
    res["baseline"] = by
    # paired variants
    var = {}
    for v in LABELS:
        rv = read(v)
        if not rv:
            continue
        for reg in ("US", "CAN"):
            dl, fz, dg, df1, nsame = [], [], [], [], 0
            for (f, d), b in base.items():
                if man[f]["region"] != reg or (f, d) not in rv:
                    continue
                r = rv[(f, d)]
                if r.get("same_as_base"):
                    nsame += 1
                    dl.append(0.0); dg.append(0.0); df1.append(0.0); fz.append(f)
                    continue
                if "error" in r:
                    continue
                if v == "feds":
                    # FEDS changes the observation too: compare L under each DOB
                    pass
                h, hb = r["17h"], b["17h"]
                dl.append(L(h["pred_ha"], h["obs_ha"]) - L(hb["pred_ha"], hb["obs_ha"]))
                dg.append(lgc(h["growth_ratio"]) - lgc(hb["growth_ratio"]))
                df1.append((h["f1"] if h["f1"] == h["f1"] else 0) - (hb["f1"] if hb["f1"] == hb["f1"] else 0))
                fz.append(f)
            if not dl:
                continue
            n_changed = len(dl) - nsame
            ch = [x for x, s in zip(dl, range(len(dl)))]
            var.setdefault(v, {})[reg] = {"label": LABELS[v], "dL": boot(dl, fz), "dlog10_gc": boot(dg, fz),
                                          "dF1": boot(df1, fz), "n_fire_days": len(dl), "n_changed": n_changed}
    res["variants"] = var
    # (c) stratification
    strat = {}
    for reg in ("US", "CAN"):
        rr = [r for r in rows if r["region"] == reg]
        if not rr:
            continue
        s = {}
        for lo, hi, name in ((0, .25, "stage<0.25"), (.25, .6, "0.25-0.6"), (.6, 1.01, "stage>=0.6")):
            q = [r for r in rr if lo <= r["stage"] < hi]
            s[name] = boot([r["L"] for r in q], [r["fire_id"] for r in q])
        q = [r for r in rr if r["days_after_containment"] == r["days_after_containment"]]
        if q:
            s["before_containment"] = boot([r["L"] for r in q if r["days_after_containment"] < 0],
                                           [r["fire_id"] for r in q if r["days_after_containment"] < 0])
            s["on_or_after_containment"] = boot([r["L"] for r in q if r["days_after_containment"] >= 0],
                                                [r["fire_id"] for r in q if r["days_after_containment"] >= 0])
        so = [r for r in rr if r["static_obs"] is not None and r["static_model"] is not None]
        s["static_share_obs_median"] = float(np.median([r["static_obs"] for r in so])) if so else None
        s["static_share_model_median"] = float(np.median([r["static_model"] for r in so])) if so else None
        s["static_gap_mean"] = boot([r["static_obs"] - r["static_model"] for r in so], [r["fire_id"] for r in so])
        # within-fire slope of L on stage (demeaned by fire)
        fids = sorted({r["fire_id"] for r in rr})
        x, y, ff = [], [], []
        for f in fids:
            q = [r for r in rr if r["fire_id"] == f]
            if len(q) < 2:
                continue
            mx, my = np.mean([r["stage"] for r in q]), np.mean([r["L"] for r in q])
            for r in q:
                x.append(r["stage"] - mx); y.append(r["L"] - my); ff.append(f)
        if len(x) > 5:
            x, y, ff = np.array(x), np.array(y), np.array(ff)
            def slope(ii, x=x, y=y):
                return float((x[ii] * y[ii]).sum() / (x[ii] ** 2).sum())
            uf = np.unique(ff); idx = {f: np.nonzero(ff == f)[0] for f in uf}
            rng = np.random.default_rng(SEED)
            bs = [slope(np.concatenate([idx[f] for f in rng.choice(uf, len(uf))])) for _ in range(NBOOT)]
            s["within_fire_slope_L_per_stage"] = [slope(np.arange(len(x))), float(np.percentile(bs, 2.5)),
                                                  float(np.percentile(bs, 97.5)), int(len(x)), int(len(uf))]
        strat[reg] = s
    # US - CAN gap decomposed by fire stage: within-stage differences (US stage weights) vs composition
    us = [r for r in rows if r["region"] == "US"]
    ca = [r for r in rows if r["region"] == "CAN"]
    if us and ca:
        def decomp(u, c):
            def sb(q, lo, hi):
                return [r for r in q if lo <= r["stage"] < hi]
            tot = np.mean([r["L"] for r in u]) - np.mean([r["L"] for r in c])
            parts, within = {}, 0.0
            for lo, hi, name in STAGE_BINS:
                uu, cc = sb(u, lo, hi), sb(c, lo, hi)
                if not uu or not cc:
                    continue
                w = len(uu) / len(u)
                d = w * (np.mean([r["L"] for r in uu]) - np.mean([r["L"] for r in cc]))
                parts[name] = d
                within += d
            return tot, within, parts
        tot, within, parts = decomp(us, ca)
        uf, cf = sorted({r["fire_id"] for r in us}), sorted({r["fire_id"] for r in ca})
        ui = {f: [r for r in us if r["fire_id"] == f] for f in uf}
        ci = {f: [r for r in ca if r["fire_id"] == f] for f in cf}
        rng = np.random.default_rng(SEED)
        bt, bw, bl = [], [], []
        for _ in range(NBOOT):
            u = [r for f in rng.choice(uf, len(uf)) for r in ui[f]]
            c = [r for f in rng.choice(cf, len(cf)) for r in ci[f]]
            t_, w_, p_ = decomp(u, c)
            bt.append(t_); bw.append(w_); bl.append(p_.get("stage>=0.6", np.nan))
        pc = lambda b: [float(np.nanpercentile(b, 2.5)), float(np.nanpercentile(b, 97.5))]
        res["gap_decomposition"] = {"US_minus_CAN_mean_L": [tot] + pc(bt),
                                    "within_stage_part": [within] + pc(bw),
                                    "composition_part": tot - within,
                                    "late_stage_contribution": [parts.get("stage>=0.6")] + pc(bl),
                                    "parts": parts,
                                    "note": "within-stage part = sum over stage bins of US share x (US mean L - CAN mean L)"}
    for reg in ("US", "CAN"):
        rr = [r for r in rows if r["region"] == reg and r["prev_share"] is not None]
        for lo, hi in SHARE_BINS:
            q = [r for r in rr if lo <= r["prev_share"] < hi]
            strat.setdefault(reg, {})[f"prev_share_{lo:g}-{min(hi, 1):g}"] = boot([r["L"] for r in q], [r["fire_id"] for r in q])
    res["suppression"] = strat
    # (e) geometry
    geo = {}
    for reg in ("US", "CAN"):
        rr = [r for r in rows if r["region"] == reg]
        if not rr:
            continue
        fz = [r["fire_id"] for r in rr]
        geo[reg] = {"L_base": boot([r["L"] for r in rr], fz),
                    "L_pred_clipped_to_final_footprint": boot([r["L_clip"] for r in rr], fz),
                    "share_pred_outside_final_footprint": boot([r["share_outside_final"] for r in rr], fz),
                    "share_pred_on_later_burned_cells": boot([r["share_later"] for r in rr], fz),
                    "L_vs_detected_only_obs": boot([r["L_det"] for r in rr], fz),
                    "dL_detected_minus_nn": boot([r["L_det"] - r["L"] for r in rr], fz),
                    "f1_detected_only": boot([r["f1_det"] for r in rr], fz)}
        for res_m in sorted({r["res_m"] for r in rr}):
            q = [r for r in rr if r["res_m"] == res_m]
            geo[reg][f"L_res_{res_m:g}m"] = boot([r["L"] for r in q], [r["fire_id"] for r in q])
    res["geometry"] = geo
    # fuel groups (US): where the modelled and observed growth lie
    grp_pred, grp_obs = {}, {}
    for (f, d), b in base.items():
        if man[f]["region"] != "US":
            continue
        for g, a in (b["17h"].get("pred_by_group_ha") or {}).items():
            grp_pred[g] = grp_pred.get(g, 0) + a
        for g, a in (b["17h"].get("obs_by_group_ha") or {}).items():
            grp_obs[g] = grp_obs.get(g, 0) + a
    res["fuel_groups_US"] = {"pred_ha": grp_pred, "obs_ha": grp_obs,
                             "pred_over_obs": {g: grp_pred.get(g, 0) / grp_obs[g] for g in grp_obs if grp_obs[g] > 0}}
    # bias by dominant observed fuel (US)
    dom = {}
    for (f, d), b in base.items():
        if man[f]["region"] != "US":
            continue
        og = b["17h"].get("obs_by_group_ha") or {}
        if not og:
            continue
        k = max(og, key=og.get)
        dom.setdefault(k, ([], []))
        dom[k][0].append(L(b["17h"]["pred_ha"], b["17h"]["obs_ha"])); dom[k][1].append(f)
    res["L_by_dominant_obs_group_US"] = {k: boot(*v) for k, v in dom.items()}
    (out / "results.json").write_text(json.dumps(res, indent=1, default=float))
    with open(out / "per_fireday.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(json.dumps({k: res[k] for k in ("baseline",)}, indent=1, default=float)[:3000])
    for v, d in var.items():
        for reg, x in d.items():
            print(f"{v:12s} {reg:4s} dL {x['dL'][0]:+.3f} [{x['dL'][1]:+.3f},{x['dL'][2]:+.3f}] n={x['n_fire_days']} changed={x['n_changed']}")


if __name__ == "__main__":
    main()

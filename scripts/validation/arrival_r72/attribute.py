#!/usr/bin/env python3
"""[R72] stage 1: ranked attribution of the C1b community-arrival error from the sensitivity runs.

Purpose
    Turn the paired one-factor runs (sensitivity.py) and the observational diagnostics
    (diagnose.py, weather_obs.py) into a ranked attribution table with 95 % whole-fire bootstrap
    CIs, the per-community dominant factor, and the [R53]/v8 jump share of observed arrivals.
    Exploratory: the C1b fire-days are already scored; nothing here is a test.

Method
    Metric: the registered C1b interval-censored arrival error (communities_c1b), per fire-day the
    mean over its reached communities, start mask = the stored baseline's (all variants score the
    same communities). For each variant family, delta = error(variant) - error(base) per fire-day;
    mean and 95 % percentile bootstrap over whole fires (2,000 draws, default_rng(20261029)).
    ``wdir_toward`` has one run per community: each community is scored on its own run.
    Explained share = -mean delta / mean base error. Hits = arrival <= 18 h. Populations: primary
    (14 fire-days / 11 fires, 20 communities: every FireSim C1b run; the registered primary used 11
    days where WISE also ran), calibration (Horse River 6 days, descriptive) and all 20 days.
    The irreducible part: a community reached before the 06:00 start (hi < 0) cannot score below
    -hi h for any model that starts at 06:00.
    Jump share: communities whose observed arrival is a v8 jump (diagnose.arrival_jump) over those
    with a zone detection on day n; Wilson 95 % interval.

Outputs
    OUT/attribution.json, OUT/attribution_table.csv, OUT/per_community.csv
Usage
    python3 scripts/validation/arrival_r72/attribute.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as K  # noqa: E402

LABELS = {
    "bp_off": "Burning period removed (24 h spread)", "bp_10_24": "Burning period 10-24 h",
    "bp_06_24": "Burning period 06-24 h", "perimeter": "Whole start perimeter active",
    "active3": "Active edges from 3 days", "ros1.5": "ROS x 1.5", "ros2": "ROS x 2",
    "ros3": "ROS x 3", "ros4": "ROS x 4", "ws1.5": "Wind speed x 1.5",
    "wdir_toward": "Wind direction toward the community (oracle)",
    "cured_leafless": "Grass 90 % cured, leafless (no green-up)",
    "fuel2014b": "CFS 2014b fuel grid instead of back-cast", "no_spinup": "No FFMC spin-up",
    "spotting": "FireSim ember spotting on", "bp_off+perimeter": "No burning period + whole perimeter",
    "bp_off+ros1.5": "No burning period + ROS x 1.5", "station_wind": "Nearest-station wind (speed + direction)",
    "station_speed": "Nearest-station wind speed", "station_dir": "Nearest-station wind direction",
}


def load_rows():
    rows = [json.loads(x) for x in (K.OUT / "sensitivity_rows.jsonl").read_text().splitlines() if x.strip()]
    return [r for r in rows if "error" not in r]


def community_errors(rows):
    """{(fire_day, family): {community: (model_h, err_h, hit)}} with wdir_toward per community."""
    out = defaultdict(dict)
    for r in rows:
        fam = r["family"]
        for c in r["rows"]:
            if fam == "wdir_toward" and r["variant"] != f"wdir_toward:{c['name']}":
                continue
            out[(r["fire_day"], fam)][c["name"]] = (c["model_h"], c["err_h"], c["hit"], c["lo"], c["hi"])
    return out


def arrival_rows(rows):
    return rows


def main():
    rows = load_rows()
    ce = community_errors(rows)
    pops = {f"{f}:{d}": p for f, d, p in K.all_days()}
    fams = sorted({k[1] for k in ce})
    base = {fd: v for (fd, fam), v in ce.items() if fam == "base"}
    f1 = defaultdict(dict)
    for r in rows:
        if r["family"] != "wdir_toward":
            f1[r["family"]][r["fire_day"]] = r["f1_17h"]
    res = {"populations": {}, "notes": "C1b metric, start mask of the stored baseline; delta = variant - base"}
    table = []
    for popname, sel in (("primary", {"primary"}), ("calibration", {"calibration"}),
                         ("all", {"primary", "calibration"})):
        days = [fd for fd in base if pops[fd] in sel]
        bmean = {fd: float(np.mean([v[1] for v in base[fd].values()])) for fd in days if base[fd]}
        n_comm = sum(len(base[fd]) for fd in days)
        b_all = K.boot_fire({tuple(fd.split(":")): v for fd, v in bmean.items()})
        pr = {"n_fire_days": len(bmean), "n_communities": n_comm, "base_mean_err_h": b_all,
              "base_hits": int(sum(v[2] for fd in days for v in base[fd].values())),
              "irreducible_h_mean": float(np.mean([max(0.0, -v[4]) for fd in days for v in base[fd].values()])) if n_comm else math.nan,
              "variants": {}}
        for fam in fams:
            if fam == "base":
                continue
            per, hits, ncomm, fixed = {}, 0, 0, 0
            for fd in days:
                v = ce.get((fd, fam))
                if not v or not base[fd]:
                    continue
                names = [n for n in base[fd] if n in v]
                if not names:
                    continue
                per[tuple(fd.split(":"))] = float(np.mean([v[n][1] for n in names])) - float(np.mean([base[fd][n][1] for n in names]))
                hits += sum(v[n][2] for n in names)
                ncomm += len(names)
                fixed += sum((not base[fd][n][2]) and v[n][2] for n in names)
            if not per:
                continue
            b = K.boot_fire(per)
            df1 = {k: f1[fam][fd] - f1["base"][fd] for fd in days if fd in f1[fam] and fd in f1["base"]
                   for k in [tuple(fd.split(":"))]}
            bf = K.boot_fire(df1) if df1 else None
            ent = {"label": LABELS.get(fam, fam), "delta_err_h": b, "n_communities": ncomm, "hits": int(hits),
                   "misses_turned_hits": int(fixed),
                   "explained_share": -b["mean"] / b_all["mean"] if b_all["mean"] else math.nan,
                   "delta_f1_17h": bf}
            pr["variants"][fam] = ent
            table.append({"population": popname, "variant": fam, "label": ent["label"],
                          "n_fire_days": b["n_days"], "n_fires": b["n_fires"], "n_communities": ncomm,
                          "delta_err_h": round(b["mean"], 3), "ci_lo": round(b["lo"], 3), "ci_hi": round(b["hi"], 3),
                          "explained_share": round(ent["explained_share"], 3), "hits": int(hits),
                          "base_hits": pr["base_hits"], "misses_turned_hits": int(fixed),
                          "delta_f1_17h": round(bf["mean"], 4) if bf else None,
                          "f1_ci_lo": round(bf["lo"], 4) if bf else None, "f1_ci_hi": round(bf["hi"], 4) if bf else None})
        pr["ranked"] = sorted(pr["variants"], key=lambda k: pr["variants"][k]["delta_err_h"]["mean"])
        res["populations"][popname] = pr
    # per community: base and each variant; dominant single factor (non-combination, non-oracle ladder)
    diag = {(r["fire_day"], r["name"]): r for r in json.loads((K.OUT / "diagnose_rows.json").read_text())
            if not r.get("summary")}
    single = [f for f in fams if f not in ("base", "bp_off+perimeter", "bp_off+ros1.5")]
    pc = []
    for fd in base:
        for n, (m, e, h, lo, hi) in base[fd].items():
            rec = {"fire_day": fd, "population": pops[fd], "community": n, "lo": lo, "hi": hi,
                   "base_model_h": m, "base_err_h": e}
            best, bestv = None, e
            for fam in fams:
                v = ce.get((fd, fam), {}).get(n)
                if v is None or fam == "base":
                    continue
                rec[f"err_{fam}"] = round(v[1], 3)
                if fam in single and v[1] < bestv - 1e-6:
                    best, bestv = fam, v[1]
            rec["best_single_factor"] = best
            rec["best_single_err_h"] = round(bestv, 3)
            d = diag.get((fd, n), {})
            j = d.get("jump", {})
            rec.update({"D0_km": round(d.get("D0_m", math.nan) / 1000, 2),
                        "nearest_start_active": d.get("nearest_start_active"),
                        "burning_class": d.get("burning_class"), "arrival_class": j.get("class"),
                        "zone_dist_to_v8_perimeter_km": (round(j["zone_dist_to_v8_perimeter_m"] / 1000, 2)
                                                         if j.get("zone_dist_to_v8_perimeter_m") is not None else None),
                        "F_need": d.get("F_need"), "wind_angle_bp_deg": d.get("wind_bp", {}).get("angle_ws_weighted_deg")})
            ros_need = None
            for k in ("ros1.5", "ros2", "ros3", "ros4"):
                v = ce.get((fd, k), {}).get(n)
                if v is not None and v[1] <= 1e-9 + 0.0:
                    ros_need = float(k[3:])
                    break
            rec["ros_factor_to_interval"] = ros_need
            pc.append(rec)
    # jump share
    det = [r for r in pc if r["arrival_class"] not in (None, "no zone detection on day n")]
    for popname, sel in (("primary", {"primary"}), ("all", {"primary", "calibration"})):
        dd = [r for r in det if r["population"] in sel]
        k = sum(r["arrival_class"] == "v8 jump" for r in dd)
        res["populations"][popname]["jump_share"] = {
            "k_jump": k, "n_timed": len(dd), "share": k / len(dd) if dd else math.nan,
            "wilson95": K.wilson(k, len(dd)),
            "n_untimed_on_day": sum(r["population"] in sel and r["arrival_class"] == "no zone detection on day n" for r in pc),
            "classes": {c: sum(r["arrival_class"] == c for r in dd) for c in sorted({r["arrival_class"] for r in dd})}}
    # jump-rule interval sensitivity (coordinator item 5): arrival epochs vs the v8 <= 12 h interval,
    # and the v8 catalogue jumps on day n under <= 12 / <= 14 / <= 24 h
    dg = json.loads((K.OUT / "diagnose_rows.json").read_text())
    for popname, sel in (("primary", {"primary"}), ("all", {"primary", "calibration"})):
        arr = [r for r in dg if not r.get("summary") and r["population"] in sel and "interval_h" in r["jump"]]
        days_ = [r for r in dg if r.get("summary") and r["population"] in sel]
        base_rule = lambda j: (j["dist_m"] >= 1000) and (j["dist_m"] <= 15000) and j["line_ok"] and j["inside_final"]  # noqa: E731
        js = res["populations"][popname]["jump_share"]
        js["arrival_epoch_interval_h"] = sorted(round(r["jump"]["interval_h"], 2) for r in arrival_rows(arr))
        js["n_arrival_epoch_interval_gt_12h"] = sum(r["jump"]["interval_h"] > 12 for r in arr)
        js["n_arrival_epoch_interval_gt_14h"] = sum(r["jump"]["interval_h"] > 14 for r in arr)
        for lim in (12, 14, 24):
            k = sum(r["jump"].get("class") == "v8 jump" and r["jump"]["interval_h"] <= lim for r in arr)
            js[f"k_jump_arrival_int{lim}h"] = k
            js[f"wilson95_int{lim}h"] = K.wilson(k, len(arr))
            js[f"day_level_v8_jumps_int{lim}h"] = int(sum(base_rule(j) and j["interval_h"] <= lim
                                                          for d in days_ for j in d["v8_jumps_day_rows"]))
        js["n_fire_days"] = len(days_)
    (K.OUT / "attribution.json").write_text(json.dumps(res, indent=1, default=float))
    with open(K.OUT / "attribution_table.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)
    keys = sorted({k for r in pc for k in r}, key=lambda k: (not k.startswith(("fire", "pop", "comm", "lo", "hi", "base")), k))
    with open(K.OUT / "per_community.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(pc)
    p = res["populations"]["primary"]
    print("primary base", p["base_mean_err_h"], "hits", p["base_hits"], "/", p["n_communities"])
    for k in p["ranked"]:
        v = p["variants"][k]
        d = v["delta_err_h"]
        print(f"{k:18s} {d['mean']:+.2f} [{d['lo']:+.2f}, {d['hi']:+.2f}] share {v['explained_share']:.2f} hits {v['hits']} "
              f"dF1 {v['delta_f1_17h']['mean'] if v['delta_f1_17h'] else float('nan'):+.3f}")
    print("jump share", res["populations"]["primary"]["jump_share"], res["populations"]["all"]["jump_share"])


def candidates_summary():
    """Descriptive summary of candidates.py (ISI-conditioned burning period vs operational and no
    burning period): arrival error (C1b primary), 17 h F1 and log10 growth-area ratio, paired deltas
    vs the operational run with 95 % whole-fire bootstrap CIs. Not a test; ARRIVAL1 was not pursued."""
    res = {}
    for mode in ("arrival", "area"):
        p = K.OUT / f"candidates_{mode}.jsonl"
        if not p.exists():
            continue
        by = defaultdict(dict)
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                by[r["variant"]][r["fire_day"]] = r
        base = by["base"]
        res[mode] = {"n_fire_days": len(base),
                     "base_f1_17h": float(np.mean([r["f1_17h"] for r in base.values()])),
                     "base_log10_area_ratio": float(np.mean([r["log10_area_ratio"] for r in base.values()]))}
        for v in by:
            if v == "base":
                continue
            sel = [fd for fd in base if fd in by[v] and (mode == "area" or base[fd]["population"] == "primary")]
            k = lambda fd: tuple(fd.split(":"))  # noqa: E731
            e = {"delta_f1_17h": K.boot_fire({k(fd): by[v][fd]["f1_17h"] - base[fd]["f1_17h"] for fd in sel}),
                 "delta_log10_area": K.boot_fire({k(fd): by[v][fd]["log10_area_ratio"] - base[fd]["log10_area_ratio"] for fd in sel})}
            if mode == "arrival":
                e["delta_err_h"] = K.boot_fire({k(fd): by[v][fd]["mae_h"] - base[fd]["mae_h"] for fd in sel})
            res[mode][v] = e
    (K.OUT / "candidates_summary.json").write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    main()
    candidates_summary()

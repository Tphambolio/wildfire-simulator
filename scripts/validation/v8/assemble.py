"""v8 gate and final assembly (PREREG_LR8 sections 7, 8).

``gate``    G1 for every drawn eligible decision fire, from model INPUTS only (M8 predicted detected jumps with the
            training beta; no outcome is read): G1a <= 100 per 12 h interval in >= 95 % of the fire's intervals;
            G1b per-active-hour mean within the training range widened x/÷ 10. G2 (share of emitted embers from
            source conditions outside the training 1st-99th percentiles) is reported. Writes ``final/gate.json``.
``unseal``  run once, after M8, B1 and the ELMFIRE subset have run on every drawn fire: checks every sealed
            file's sha256 against ``draw/sealed_manifest.csv`` (and the manifest against ``draw/state.json``),
            maps each jump to its interval and 375 m candidate cell, and writes
            ``final/candidates/<fire>__<epoch>.npz`` (cell_id, score_M8, score_B1, score_B1all, score_ELMFIRE
            where run), ``final/jumps.csv`` and ``final/counts.csv`` for ``scripts/validation/v8_score.py``.
            jumps.csv carries every cluster that passes the primary rule except the final-perimeter condition,
            with ``out_final_m`` (addendum A1): v8_score.py ranks only members within 375 m and counts the rest
            (never reached > 540 m; 375-540 m neither). Jumps whose interval was not run are counted here.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from . import common as C
from .intervals import b1_score, block_of, m8_score

FINAL = C.DATA / "final"


def beta():
    return json.loads((C.DATA / "fit" / "fit_beta.json").read_text())["beta"]


def drawn_eligible():
    c = pd.read_csv(C.DATA / "draw" / "counts.csv")
    return c[c.eligible.astype(bool)]


def gate():
    FINAL.mkdir(parents=True, exist_ok=True)
    b = beta()
    rng = json.loads((C.DATA / "fit" / "gate_ranges.json").read_text())
    out = {}
    for r in drawn_eligible().itertuples():
        fd = C.FIRES / r.fire_key.replace(":", "_")
        done = fd / "intervals" / "_done.json"
        if not done.exists():
            out[r.fire_key] = {"G1_pass": False, "reason": "intervals not run", "decision": True}
            continue
        info = json.loads(done.read_text())
        if info.get("status") != "ok" or not info["intervals"]:
            out[r.fire_key] = {"G1_pass": True, "reason": "no valid interval (contributes no jumps)", "decision": True}
            continue
        per12, tot, act_h = [], 0.0, 0.0
        for it in info["intervals"]:
            lam = float(m8_score(np.load(fd / "intervals" / f"{it['epoch']:05d}.npz"), b).sum())
            per12.append(lam * 12.0 / max(it["hours"], 1.0))
            tot += lam
            act_h += it["hours"] if it["n_active"] > 0 else 0.0
        share = float(np.mean(np.array(per12) <= rng["G1a_ceiling_per_12h"]))
        pah = tot / max(act_h, 1e-9)
        g1a = share >= rng["G1a_share_required"]
        g1b = rng["G1b_low"] <= pah <= rng["G1b_high"]
        out[r.fire_key] = {"G1_pass": bool(g1a and g1b), "G1a_share_le100": share, "per_active_hour": pah,
                           "G1a": bool(g1a), "G1b": bool(g1b), "decision": True}
    (FINAL / "gate.json").write_text(json.dumps(out, indent=1))
    fails = [k for k, v in out.items() if not v["G1_pass"]]
    print(f"G1 failing: {len(fails)} of {len(out)}")
    return out


def unseal():
    from . import elmfire as E
    st = json.loads((C.DATA / "draw" / "state.json").read_text())
    man_p = C.DATA / "draw" / "sealed_manifest.csv"
    if st.get("sealed_manifest_sha256") != C.sha256(man_p):
        raise RuntimeError("sealed manifest changed since the draw")
    man = pd.read_csv(man_p).drop_duplicates("fire_key", keep="last")
    sealed_dir = C.DATA / "draw" / "sealed"
    parts = []
    for m in man.itertuples():
        f = sealed_dir / m.file
        if C.sha256(f) != m.sha256:
            raise RuntimeError(f"sealed file {f.name} changed since the draw")
        if f.stat().st_size > 1:
            try:
                parts.append(pd.read_csv(f, parse_dates=["t_start", "t_end"]))
            except pd.errors.EmptyDataError:
                pass
    FINAL.mkdir(parents=True, exist_ok=True)
    (FINAL / "candidates").mkdir(exist_ok=True)
    b = beta()
    J = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["fire_key", "t_end", "line_ok", "inside_final", "sep_ok", "interval_h", "dist_m", "out_final_m", "x_utm", "y_utm", "gap", "year"])
    # primary rule without the final-perimeter condition (addendum A1: v8_score.py applies the guard)
    J = J[J.line_ok & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000) & (J.dist_m <= 15000)]
    el = drawn_eligible()
    sub = set(E.subset(list(el.fire_key)))
    rows, excl = [], {"no_interval": 0, "not_candidate": 0, "fire_not_run": 0}
    for r in el.itertuples():
        fd = C.FIRES / r.fire_key.replace(":", "_")
        done = fd / "intervals" / "_done.json"
        jk = J[J.fire_key == r.fire_key]
        if not done.exists() or json.loads(done.read_text()).get("status") != "ok":
            excl["fire_not_run"] += len(jk)  # model failure or no detections: every interval dropped, jumps counted
            for j in jk.itertuples():
                rows.append(dict(fire_key=r.fire_key, region=r.region, interval=f"x{pd.Timestamp(j.t_end):%Y%m%d%H%M}",
                                 cell_id=-1, out_final_m=0.0 if bool(j.inside_final) else float(j.out_final_m),
                                 dist_m=float(j.dist_m), gap=j.gap, year=int(j.year)))
            continue
        info = json.loads(done.read_text())
        meta = json.loads((fd / "meta.json").read_text())
        by_end = {pd.Timestamp(it["t_end"]): it for it in info.get("intervals", [])}
        for it in info.get("intervals", []):
            z = np.load(fd / "intervals" / f"{it['epoch']:05d}.npz")
            bnx = info["block_shape"][1]
            arr = dict(cell_id=(z["bj"].astype(np.int64) * bnx + z["bi"]), score_M8=m8_score(z, b),
                       score_B1=b1_score(z, True), score_B1all=b1_score(z, False))
            if r.fire_key in sub:
                e = E.block_scores(r.fire_key, meta, it["epoch"])
                if e is not None:
                    arr["score_ELMFIRE"] = e
            np.savez(FINAL / "candidates" / f"{r.fire_key}__{it['epoch']}.npz", **arr)
        for j in jk.itertuples():
            it = by_end.get(pd.Timestamp(j.t_end))
            if it is None:  # interval not valid on the run grid (no start perimeter): kept for v8_score to count
                rows.append(dict(fire_key=r.fire_key, region=r.region, interval=f"x{pd.Timestamp(j.t_end):%Y%m%d%H%M}",
                                 cell_id=-1, out_final_m=0.0 if bool(j.inside_final) else float(j.out_final_m),
                                 dist_m=float(j.dist_m), gap=j.gap, year=int(j.year)))
                excl["no_interval"] += 1
                continue
            bj, bi = block_of(meta, j.x_utm, j.y_utm)
            rows.append(dict(fire_key=r.fire_key, region=r.region, interval=str(it["epoch"]),
                             cell_id=int(bj * info["block_shape"][1] + bi),
                             out_final_m=0.0 if bool(j.inside_final) else float(j.out_final_m),
                             dist_m=float(j.dist_m), gap=j.gap, year=int(j.year)))
    pd.DataFrame(rows, columns=["fire_key", "region", "interval", "cell_id", "out_final_m", "dist_m", "gap", "year"]).to_csv(FINAL / "jumps.csv", index=False)
    el[["fire_key", "region", "K"]].to_csv(FINAL / "counts.csv", index=False)
    (FINAL / "unseal_exclusions.json").write_text(json.dumps(excl, indent=1))
    print("unsealed", len(rows), excl)


if __name__ == "__main__":
    C.guard()
    {"gate": gate, "unseal": unseal}[sys.argv[1]]()

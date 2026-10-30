#!/usr/bin/env python3
"""v8_score.py -- the deciding analysis of PREREG_LR8 (long-range embers v8, target L).

Purpose
    Implements exactly docs/PREREG_LR8.md sections 6-7: within-fire-interval percentile ranks of jump
    cells among candidate cells (ties = half, Mann-Whitney), per-fire mean rank difference a_f, the
    cluster-weighted statistic S3 (w_f = K_f / (1 + 0.3 (K_f - 1))), S1/S2 weightings (reported), the
    fire-level one-sided sign-flip randomisation test of S3 (20,000 flips within regions, seed S_TEST),
    the stratified fire bootstrap 95 % interval (2,000 draws, seed S_BOOT), the region floor
    AUC(M8) >= 0.60 (S3-weighted point estimate), the K_min / per-region rule and the transfer gate G1
    (> 10 % of decision fires failing G1 fails v8). Nothing here fits or tunes anything.

Inputs (one directory, written by the v8 run scripts; read once, after every model has run)
    candidates/<fire>__<interval>.npz   cell_id (int64), score_M8, score_B1 [, score_ELMFIRE] (float64)
                                        for every candidate cell of the interval (PREREG section 6)
    jumps.csv                           fire_key, region, interval, cell_id, out_final_m (the unsealed jump
                                        catalogue; addendum A1: every otherwise-qualifying cluster, with the
                                        distance of its member outside the final perimeter, 0 if inside)
    gate.json                           {"<fire_key>": {"G1_pass": bool, ...}} (computed from inputs only)
    counts.csv                          fire_key, region, K (the per-block counts used for the stopping rule)
Outputs
    scores_PREREG_LR8.json  (decision, S3 with p-value and bootstrap CI, region AUCs, S1/S2, per-fire table,
                            ELMFIRE comparison if present -- never deciding)
Addendum A1 (PREREG_LR8, 2026-10-24): never-reached guard (section 8a.2). A jumps.csv row whose member is
    > 540 m outside the final perimeter is "never reached" and a row 375-540 m outside is neither positive nor
    negative: neither is ever ranked. Rows within 375 m are the positives. Every row is accounted for, per fire
    and in total: ranked, never reached, 375-540 m, interval excluded (no candidate file), cell not a candidate.
    A jumps.csv without out_final_m is refused.
Usage
    python3 scripts/validation/v8_score.py score --in DIR --out FILE
    python3 scripts/validation/v8_score.py selftest      # unit tests required before the freeze
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

S_TEST, S_BOOT = 2026101803, 2026101804
N_FLIPS, N_BOOT = 20_000, 2_000
RHO_PRE = 0.30
K_MIN, K_REG = 150, 60
FLOOR = 0.60
ALPHA = 0.025
G1_MAX_FAIL = 0.10
FINAL_TOL, NEVER_M = 375.0, 540.0   # section 8a.2: positives within 375 m; > 540 m outside = never reached
ACCOUNT_KEYS = ("rows", "ranked", "never_reached_gt540m", "outside_375_540m", "interval_excluded", "not_candidate")


def final_class(out_final_m: float) -> str:
    """Addendum A1 / section 8a.2 class of a jump by its member's distance outside the final perimeter (m)."""
    x = float(out_final_m)
    if not np.isfinite(x):
        raise ValueError("out_final_m must be finite")
    if x <= FINAL_TOL:
        return "positive"
    return "never_reached_gt540m" if x > NEVER_M else "outside_375_540m"


def pct_rank(score_all: np.ndarray, score_at: np.ndarray) -> np.ndarray:
    """Percentile rank of each value in score_at among score_all; ties count half (Mann-Whitney)."""
    s = np.sort(score_all)
    lo = np.searchsorted(s, score_at, side="left")
    hi = np.searchsorted(s, score_at, side="right")
    return (lo + 0.5 * (hi - lo)) / len(s)


def weights(K: np.ndarray, stat: str = "S3") -> np.ndarray:
    K = np.asarray(K, float)
    has = (K > 0).astype(float)
    if stat == "S1":
        return has
    if stat == "S2":
        return K
    return K / (1.0 + (np.maximum(K, 1) - 1.0) * RHO_PRE) * has


def signflip_p(a: np.ndarray, w: np.ndarray, region: np.ndarray, flips: int = N_FLIPS, seed: int = S_TEST) -> float:
    """One-sided p of the weighted mean of a > 0 under fire-level sign flips (flips drawn within regions:
    each fire's sign is independent, so stratification only fixes the order of draws)."""
    keep = w > 0
    a, w, region = a[keep], w[keep], region[keep]
    t = (w * a).sum() / w.sum()
    rng = np.random.default_rng(seed)
    order = np.argsort(region, kind="stable")
    wa = (w * a)[order]
    ge = 0
    for start in range(0, flips, 2000):
        n = min(2000, flips - start)
        S = rng.choice((-1.0, 1.0), size=(n, len(wa)))
        ge += int(((S @ wa) / w.sum() >= t).sum())
    return (1 + ge) / (flips + 1)


def boot_ci(a, w, region, B=N_BOOT, seed=S_BOOT):
    rng = np.random.default_rng(seed)
    est = []
    regs = np.unique(region)
    idx = [np.where(region == r)[0] for r in regs]
    for _ in range(B):
        sel = np.concatenate([rng.choice(ix, len(ix)) for ix in idx])
        est.append((w[sel] * a[sel]).sum() / max(w[sel].sum(), 1e-12))
    return float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))


def per_fire(cands: dict, jumps: list[tuple], models: list[str], accounting: dict | None = None) -> dict:
    """cands: {(fire, interval): {"cell_id": arr, "score_<m>": arr}};
    jumps: [(fire, region, interval, cell, out_final_m)] (addendum A1; 4-tuples without out_final_m are refused).
    ``accounting`` (optional dict) receives, per fire, the count of rows in each ACCOUNT_KEYS class."""
    out = {}
    acc = accounting if accounting is not None else {}
    for row in jumps:
        if len(row) != 5:
            raise ValueError("jump rows must carry out_final_m (PREREG_LR8 addendum A1, never-reached guard)")
        fire, region, interval, cell, out_m = row
        a = acc.setdefault(fire, dict({k: 0 for k in ACCOUNT_KEYS}, region=region))
        a["rows"] += 1
        cls = final_class(out_m)
        if cls != "positive":
            a[cls] += 1  # never ranked (section 8a.2); enters the count / Brier secondaries only
            continue
        c = cands.get((fire, interval))
        if c is None:
            a["interval_excluded"] += 1  # interval excluded (section 8): counted here
            continue
        pos = np.where(c["cell_id"] == cell)[0]
        if len(pos) == 0:
            a["not_candidate"] += 1  # jump cell not a candidate (non-fuel, burned, outside box): counted here
            continue
        a["ranked"] += 1
        rec = out.setdefault(fire, {"region": region, "r": {m: [] for m in models}})
        for m in models:
            sc = c.get(f"score_{m}")
            if sc is not None:
                rec["r"][m].append(float(pct_rank(sc, sc[pos[:1]])[0]))
    return out


def accounting_summary(acc: dict) -> dict:
    """Totals and by-region totals of the per-fire jump accounting (addendum A1)."""
    tot = {k: int(sum(v[k] for v in acc.values())) for k in ACCOUNT_KEYS}
    regs = sorted({v["region"] for v in acc.values()})
    by = {r: {k: int(sum(v[k] for v in acc.values() if v["region"] == r)) for k in ACCOUNT_KEYS} for r in regs}
    # count secondary (section 8a.2): positives in scored or unscored intervals plus never-reached clusters
    for d in [tot] + list(by.values()):
        d["count_secondary"] = d["ranked"] + d["interval_excluded"] + d["not_candidate"] + d["never_reached_gt540m"]
    return {"total": tot, "by_region": by,
            "per_fire": {f: {k: int(v[k]) for k in ACCOUNT_KEYS} for f, v in sorted(acc.items())}}


def decide(pf: dict, gate: dict | None = None, counts: dict | None = None) -> dict:
    fires = sorted(pf)
    region = np.array([pf[f]["region"] for f in fires])
    K = np.array([len(pf[f]["r"]["M8"]) for f in fires], float)
    rM = [np.array(pf[f]["r"]["M8"]) for f in fires]
    rB = [np.array(pf[f]["r"]["B1"]) for f in fires]
    a = np.array([(m - b).mean() if len(m) else 0.0 for m, b in zip(rM, rB)])
    aucM = np.array([m.mean() if len(m) else np.nan for m in rM])
    aucB = np.array([b.mean() if len(b) else np.nan for b in rB])
    res = {"n_fires_with_jumps": int((K > 0).sum()), "K_total": int(K.sum()),
           "K_by_region": {r: int(K[region == r].sum()) for r in np.unique(region)}}
    for st in ("S1", "S2", "S3"):
        w = weights(K, st)
        res[st] = {"est": float((w * a).sum() / w.sum()) if w.sum() else None}
    w3 = weights(K, "S3")
    res["S3"]["p_signflip"] = signflip_p(a, w3, region)
    res["S3"]["boot95"] = boot_ci(a, w3, region)
    res["region_auc_M8"] = {r: float(np.nansum(w3[region == r] * aucM[region == r]) / w3[region == r].sum())
                            for r in np.unique(region)}
    res["region_auc_B1"] = {r: float(np.nansum(w3[region == r] * aucB[region == r]) / w3[region == r].sum())
                            for r in np.unique(region)}
    kc = counts or res["K_by_region"]
    c1 = sum(kc.values()) >= K_MIN and all(v >= K_REG for v in kc.values())
    if gate is not None:
        dec = [f for f in gate if gate[f].get("decision", True)]
        g1_fail = float(np.mean([not gate[f]["G1_pass"] for f in dec])) if dec else 0.0
    else:
        g1_fail = None
    c2 = g1_fail is not None and g1_fail <= G1_MAX_FAIL
    c3 = res["S3"]["p_signflip"] < ALPHA and res["S3"]["est"] > 0
    c4 = all(v >= FLOOR for v in res["region_auc_M8"].values())
    res["criteria"] = {"1_K_min": bool(c1), "2_gate_G1": bool(c2), "G1_fail_share": g1_fail,
                       "3_signflip": bool(c3), "4_region_floor": bool(c4)}
    if not c1:
        res["decision"] = "NOT DECIDABLE"
    elif not c2:
        res["decision"] = "FAIL (establishment did not transfer: G1)"
    else:
        res["decision"] = "PASS" if (c3 and c4) else "FAIL"
    res["per_fire"] = [{"fire_key": f, "region": r, "K": int(k), "a_f": float(x), "auc_M8": float(m), "auc_B1": float(b)}
                       for f, r, k, x, m, b in zip(fires, region, K, a, aucM, aucB)]
    return res


def load_dir(d: Path):
    import csv
    cands = {}
    for p in sorted((d / "candidates").glob("*.npz")):
        fire, interval = p.stem.split("__")
        z = np.load(p)
        cands[(fire, interval)] = {k: z[k] for k in z.files}
    rd = csv.DictReader(open(d / "jumps.csv"))
    if "out_final_m" not in (rd.fieldnames or []):
        raise ValueError("jumps.csv has no out_final_m column: refused (PREREG_LR8 addendum A1, never-reached guard)")
    jumps = [(r["fire_key"], r["region"], r["interval"], int(r["cell_id"]), float(r["out_final_m"])) for r in rd]
    gate = json.load(open(d / "gate.json")) if (d / "gate.json").exists() else None
    counts = None
    if (d / "counts.csv").exists():
        counts = {}
        for r in csv.DictReader(open(d / "counts.csv")):
            counts[r["region"]] = counts.get(r["region"], 0) + int(r["K"])
    return cands, jumps, gate, counts


def selftest() -> None:
    """Unit tests required before the freeze (PREREG_LR8 section 9)."""
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(0)
    # 1. rank with ties == Mann-Whitney AUC (one positive per draw against all candidates incl. itself)
    for _ in range(200):
        s = rng.integers(0, 5, size=50).astype(float)
        j = rng.integers(0, 50)
        y = np.zeros(50); y[j] = 1
        neg = np.delete(s, j)
        auc = roc_auc_score(np.r_[1, np.zeros(len(neg))], np.r_[s[j], neg])
        r = pct_rank(s, s[[j]])[0]
        # pct_rank over all cells including the jump itself: r = (n_less + 0.5 n_tie_incl_self)/n
        assert abs(r * 50 - (auc * 49 + 0.5)) < 1e-9
    # 2. sign-flip null calibration (simulated, symmetric a): false-pass rate near alpha
    hits = 0
    for i in range(400):
        K = rng.negative_binomial(0.3, 0.3 / (0.3 + 0.6), size=300).astype(float)
        a = rng.normal(0, 0.15, size=300)
        reg = np.array(["CAN"] * 180 + ["US"] * 120)
        w = weights(K)
        if w.sum() == 0:
            continue
        hits += signflip_p(a, w, reg, flips=999, seed=i) < ALPHA
    assert hits / 400 < 0.05, hits / 400
    # 3. weights
    assert np.allclose(weights(np.array([0, 1, 4])), [0, 1, 4 / 1.9])
    # 4. decide() on a toy where M8 is clearly better passes; reversed fails
    pf = {f"f{i}": {"region": "CAN" if i % 2 else "US", "r": {"M8": list(rng.uniform(0.75, 0.95, 5)),
                                                              "B1": list(rng.uniform(0.5, 0.7, 5))}} for i in range(40)}
    gate = {f: {"G1_pass": True} for f in pf}
    assert decide(pf, gate)["decision"] == "PASS"
    for f in pf:
        pf[f]["r"]["M8"], pf[f]["r"]["B1"] = pf[f]["r"]["B1"], pf[f]["r"]["M8"]
    assert decide(pf, gate)["decision"].startswith("FAIL")
    # 5. addendum A1 never-reached guard ([R61] fixture: cell 7 inside, cell 42 a 700 m 'never reached' member,
    #    cell 9 450 m outside, cell 3 in an excluded interval, cell 999 not a candidate)
    cells = np.arange(100, dtype=np.int64)
    cands = {("SYN:1", "e1"): {"cell_id": cells, "score_M8": rng.uniform(size=100), "score_B1": rng.uniform(size=100)}}
    acc = {}
    rows = [("SYN:1", "CAN", "e1", 7, 0.0), ("SYN:1", "CAN", "e1", 42, 700.0), ("SYN:1", "CAN", "e1", 9, 450.0),
            ("SYN:1", "CAN", "e2", 3, 0.0), ("SYN:1", "CAN", "e1", 999, 100.0)]
    pf = per_fire(cands, rows, ["M8", "B1"], acc)
    assert len(pf["SYN:1"]["r"]["M8"]) == 1
    a = acc["SYN:1"]
    assert (a["ranked"], a["never_reached_gt540m"], a["outside_375_540m"], a["interval_excluded"], a["not_candidate"]) == (1, 1, 1, 1, 1)
    assert accounting_summary(acc)["total"]["count_secondary"] == 4
    try:
        per_fire(cands, [("SYN:1", "CAN", "e1", 7)], ["M8"])
        raise AssertionError("4-tuple accepted")
    except ValueError:
        pass
    print("selftest passed")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--in", dest="inp", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        selftest()
        return
    cands, jumps, gate, counts = load_dir(a.inp)
    models = ["M8", "B1"] + (["ELMFIRE"] if any("score_ELMFIRE" in c for c in cands.values()) else [])
    acc = {}
    pf = per_fire(cands, jumps, models, acc)
    res = decide(pf, gate, counts)
    res["jump_accounting"] = accounting_summary(acc)  # addendum A1: every jumps.csv row, by class
    if "ELMFIRE" in models:  # reported only, never deciding (PREREG section 7)
        fe = [f for f in pf if len(pf[f]["r"]["ELMFIRE"]) == len(pf[f]["r"]["M8"]) and pf[f]["r"]["ELMFIRE"]]
        if fe:
            K = np.array([len(pf[f]["r"]["M8"]) for f in fe], float)
            w = weights(K)
            reg = np.array([pf[f]["region"] for f in fe])
            for name, (x, y) in {"M8_minus_ELMFIRE": ("M8", "ELMFIRE"), "ELMFIRE_minus_B1": ("ELMFIRE", "B1")}.items():
                d = np.array([np.mean(np.array(pf[f]["r"][x]) - np.array(pf[f]["r"][y])) for f in fe])
                res[name] = {"est": float((w * d).sum() / w.sum()), "boot95": boot_ci(d, w, reg), "n_fires": len(fe)}
    json.dump(res, open(a.out, "w"), indent=1)
    print(res["decision"], {k: res["S3"][k] for k in ("est", "p_signflip")}, res["region_auc_M8"])


if __name__ == "__main__":
    main()

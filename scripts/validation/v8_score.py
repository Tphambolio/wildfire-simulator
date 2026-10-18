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
    jumps.csv                           fire_key, region, interval, cell_id (the unsealed jump catalogue)
    gate.json                           {"<fire_key>": {"G1_pass": bool, ...}} (computed from inputs only)
    counts.csv                          fire_key, region, K (the per-block counts used for the stopping rule)
Outputs
    scores_PREREG_LR8.json  (decision, S3 with p-value and bootstrap CI, region AUCs, S1/S2, per-fire table,
                            ELMFIRE comparison if present -- never deciding)
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


def per_fire(cands: dict, jumps: list[tuple], models: list[str]) -> dict:
    """cands: {(fire, interval): {"cell_id": arr, "score_<m>": arr}}; jumps: [(fire, region, interval, cell)]."""
    out = {}
    for fire, region, interval, cell in jumps:
        c = cands.get((fire, interval))
        if c is None:
            continue  # interval excluded (counted by the caller)
        pos = np.where(c["cell_id"] == cell)[0]
        if len(pos) == 0:
            continue  # jump cell not a candidate (non-fuel, burned, outside box): excluded, counted
        rec = out.setdefault(fire, {"region": region, "r": {m: [] for m in models}})
        for m in models:
            sc = c.get(f"score_{m}")
            if sc is not None:
                rec["r"][m].append(float(pct_rank(sc, sc[pos[:1]])[0]))
    return out


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
    jumps = [(r["fire_key"], r["region"], r["interval"], int(r["cell_id"])) for r in csv.DictReader(open(d / "jumps.csv"))]
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
    pf = per_fire(cands, jumps, models)
    res = decide(pf, gate, counts)
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

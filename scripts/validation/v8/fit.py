"""v8 establishment fit and transfer-gate ranges on the TRAINING split only (PREREG_LR8 sections 5, 7; addendum A2).

- Positives: the addendum-A1 training jump catalogue (``rule_A1/jumps_training.csv``, primary rule: 1-15 km, line check,
  inside the final perimeter, separate-start mask, interval <= 12 h), each matched to its interval (by end
  time) and 375 m candidate cell. Jumps whose cell is not a candidate on the run grid are counted and dropped.
- Model: lambda_c = exp(b0 + b_m (m_ff - 21)) sum_w exp(b_w ln(1 + U10 WAF_w)) (A_short,w,c + exp(b_L) A_long,w,c)
  with b_m = -0.2441 per % moisture fixed (Beverly & Wotton grass curve: P 0.1 at 31 %, 0.9 at 13 % [D]).
- Fit: penalised Poisson likelihood over every candidate cell of every valid training interval (sufficient
  statistics per interval and zone), constraints b_w >= 0, b_L <= 0, weakly informative normal priors
  b_w ~ N(1, 1), b_L ~ N(ln 0.5, 1) [H] (glowing-brand factor 0.5 of v7; Manzello 2006/2008); b0 flat.
  Fewer than 100 matched jumps -> only b0 is fitted (b_w = 1, b_L = ln 0.5).
- Gate ranges: with the fitted beta, each training fire's predicted detected jumps per interval (G1a, absolute
  ceiling 100 per 12 h interval, reported) and its mean per active-spread hour; the G1b range is the training
  5th-95th percentile of that mean, widened x/÷ 10.
- Training-only diagnostic (never deciding): S3 of M8 - B1 on the training jumps.
Outputs (``$FIRESIM_V8_DATA/fit/``): fit_beta.json, gate_ranges.json, training_diagnostic.json.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from . import common as C
from .intervals import WAF, b1_score, block_of, m8_score

B_M = (math.log(0.9 / 0.1) - math.log(0.1 / 0.9)) / (13.0 - 31.0)  # -0.2441 per % [D]
PRIOR = {"b_w": (1.0, 1.0), "b_L": (math.log(0.5), 1.0)}
OUT = C.DATA / "fit"


def primary_jumps() -> pd.DataFrame:
    J = pd.read_csv(C.RULE_OUT / "jumps_training.csv", parse_dates=["t_start", "t_end"])  # addendum-A1 catalogue
    m = J.line_ok & J.inside_final & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000) & (J.dist_m <= 15000)
    return J[m]


def collect(fire_keys):
    J = primary_jumps()
    S_rows, pos, excl = [], [], {"no_interval": 0, "not_candidate": 0, "fire_not_run": 0}
    for k in fire_keys:
        fd = C.FIRES / k.replace(":", "_")
        done = fd / "intervals" / "_done.json"
        jk = J[J.fire_key == k]
        if not done.exists():
            excl["fire_not_run"] += len(jk)
            continue
        info = json.loads(done.read_text())
        if info.get("status") != "ok":
            excl["fire_not_run"] += len(jk)
            continue
        meta = json.loads((fd / "meta.json").read_text())
        by_end = {pd.Timestamp(it["t_end"]): it for it in info["intervals"]}
        for it in info["intervals"]:
            z = np.load(fd / "intervals" / f"{it['epoch']:05d}.npz")
            nz = len(z["u10"])
            Sz = np.zeros((nz, 2, 3))
            for zi in range(nz):
                Sz[zi] = z["A"][:, :, z["zone"] == zi].sum(axis=-1)
            S_rows.append(dict(fire_key=k, epoch=it["epoch"], hours=it["hours"], active=it["n_active"] > 0,
                               S=Sz, u10=z["u10"].astype(float), m=z["m_ff"].astype(float)))
        for r in jk.itertuples():
            it = by_end.get(pd.Timestamp(r.t_end))
            if it is None:
                excl["no_interval"] += 1
                continue
            z = np.load(fd / "intervals" / f"{it['epoch']:05d}.npz")
            bj, bi = block_of(meta, r.x_utm, r.y_utm)
            hit = np.where((z["bj"] == bj) & (z["bi"] == bi))[0]
            if not len(hit):
                excl["not_candidate"] += 1
                continue
            h = hit[0]
            zi = int(z["zone"][h])
            pos.append(dict(fire_key=k, epoch=it["epoch"], A=z["A"][:, :, h].astype(float), u10=float(z["u10"][zi]),
                            m=float(z["m_ff"][zi])))
    return S_rows, pos, excl


def _terms(b_w, b_L, A, u):
    return sum(math.exp(b_w * math.log1p(u * WAF[w])) * (A[0, w] + math.exp(b_L) * A[1, w]) for w in range(3))


def fit(S_rows, pos, full=True):
    def nll(v):
        b0, b_w, b_L = v if full else (v[0], PRIOR["b_w"][0], PRIOR["b_L"][0])
        ll = 0.0
        for p in pos:
            lam = _terms(b_w, b_L, p["A"], p["u10"]) * math.exp(b0 + B_M * (p["m"] - 21.0))
            ll += math.log(max(lam, 1e-300))
        tot = 0.0
        for r in S_rows:
            for zi in range(len(r["u10"])):
                tot += _terms(b_w, b_L, r["S"][zi], r["u10"][zi]) * math.exp(B_M * (r["m"][zi] - 21.0))
        ll -= math.exp(b0) * tot
        if full:
            ll -= 0.5 * ((b_w - PRIOR["b_w"][0]) / PRIOR["b_w"][1]) ** 2
            ll -= 0.5 * ((b_L - PRIOR["b_L"][0]) / PRIOR["b_L"][1]) ** 2
        return -ll
    # b0 starting value from the counts at the prior centre
    tot0 = sum(_terms(PRIOR["b_w"][0], PRIOR["b_L"][0], r["S"][zi], r["u10"][zi]) * math.exp(B_M * (r["m"][zi] - 21.0))
               for r in S_rows for zi in range(len(r["u10"])))
    b00 = math.log(max(len(pos), 1) / max(tot0, 1e-300))
    if full:
        res = minimize(nll, [b00, PRIOR["b_w"][0], PRIOR["b_L"][0]], method="L-BFGS-B",
                       bounds=[(None, None), (0.0, 10.0), (-10.0, 0.0)])
        b = dict(b0=float(res.x[0]), b_w=float(res.x[1]), b_L=float(res.x[2]))
    else:
        res = minimize(nll, [b00], method="L-BFGS-B")
        b = dict(b0=float(res.x[0]), b_w=PRIOR["b_w"][0], b_L=PRIOR["b_L"][0])
    b["b_m"] = B_M
    return b, res


def gate_ranges(beta, fire_keys):
    rows = []
    for k in fire_keys:
        fd = C.FIRES / k.replace(":", "_")
        done = fd / "intervals" / "_done.json"
        if not done.exists():
            continue
        info = json.loads(done.read_text())
        if info.get("status") != "ok" or not info["intervals"]:
            continue
        per_int, act_h = [], 0.0
        for it in info["intervals"]:
            z = np.load(fd / "intervals" / f"{it['epoch']:05d}.npz")
            lam = float(m8_score(z, beta).sum()) * (12.0 / max(it["hours"], 1.0))
            per_int.append(lam)
            act_h += it["hours"] if it["n_active"] > 0 else 0.0
        tot = sum(float(m8_score(np.load(fd / "intervals" / f"{it['epoch']:05d}.npz"), beta).sum()) for it in info["intervals"])
        rows.append(dict(fire_key=k, n_intervals=len(per_int), max_per_12h=max(per_int),
                         share_le100=float(np.mean(np.array(per_int) <= 100)), per_active_hour=tot / max(act_h, 1e-9)))
    df = pd.DataFrame(rows)
    p5, p95 = np.percentile(df.per_active_hour, [5, 95])
    return df, dict(G1a_ceiling_per_12h=100.0, G1a_share_required=0.95, G1b_low=float(p5 / 10), G1b_high=float(p95 * 10),
                    training_p5=float(p5), training_p95=float(p95),
                    training_fires_failing_G1a=int((df.share_le100 < 0.95).sum()), n_training_fires=len(df))


def training_diagnostic(beta, pos_fire_keys):
    """S3(M8 - B1) on training jumps (training only; never deciding)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("v8_score", Path(__file__).resolve().parents[1] / "v8_score.py")
    v8s = importlib.util.module_from_spec(spec); spec.loader.exec_module(v8s)
    J = primary_jumps()
    pf = {}
    for k in sorted(set(pos_fire_keys)):
        fd = C.FIRES / k.replace(":", "_")
        info = json.loads((fd / "intervals" / "_done.json").read_text())
        meta = json.loads((fd / "meta.json").read_text())
        by_end = {pd.Timestamp(it["t_end"]): it for it in info["intervals"]}
        for r in J[J.fire_key == k].itertuples():
            it = by_end.get(pd.Timestamp(r.t_end))
            if it is None:
                continue
            z = np.load(fd / "intervals" / f"{it['epoch']:05d}.npz")
            bj, bi = block_of(meta, r.x_utm, r.y_utm)
            hit = np.where((z["bj"] == bj) & (z["bi"] == bi))[0]
            if not len(hit):
                continue
            sm, sb, sba = m8_score(z, beta), b1_score(z, True), b1_score(z, False)
            rec = pf.setdefault(k, {"region": r.region, "r": {"M8": [], "B1": [], "B1all": []}})
            for name, sc in (("M8", sm), ("B1", sb), ("B1all", sba)):
                rec["r"][name].append(float(v8s.pct_rank(sc, sc[hit[:1]])[0]))
    res = v8s.decide(pf, gate=None)
    res.pop("per_fire", None)
    return res


def active_segment_test(fire_keys):
    """Addendum A2, A2.5 (PREREG section 9 'active-segment rule test', training only): share of start-perimeter
    edge cells from which the fire advanced (a detection outside the start perimeter within 1 km in the next
    24 h), for active vs held edge cells, pooled over valid training intervals and per fire."""
    rows = []
    for k in fire_keys:
        done = C.FIRES / k.replace(":", "_") / "intervals" / "_done.json"
        if not done.exists():
            continue
        info = json.loads(done.read_text())
        for it in info.get("intervals", []):
            rows.append(dict(fire_key=k, edge=it["edge_cells"], active=it["n_active"], adv=it["edge_adv24"],
                             act_adv=it["edge_active_and_adv"]))
    df = pd.DataFrame(rows)
    if not len(df):
        return {}
    e, a, v, av = (float(df[c].sum()) for c in ("edge", "active", "adv", "act_adv"))
    held, held_adv = e - a, v - av
    return dict(n_intervals=len(df), n_fires=int(df.fire_key.nunique()), edge_cells=e, active_cells=a,
                active_share_of_edge=a / e if e else None,
                advanced_share_active=av / a if a else None, advanced_share_held=held_adv / held if held else None,
                share_of_advancing_edge_that_was_active=av / v if v else None,
                note="advance = any detection outside the start perimeter within 1 km of the edge cell in the 24 h after t_start")


def main():
    C.guard()
    OUT.mkdir(parents=True, exist_ok=True)
    split = C.split_table()
    keys = sorted(split[split.assignment.str.startswith("training")].fire_key)
    S_rows, pos, excl = collect(keys)
    full = len(pos) >= 100
    beta, res = fit(S_rows, pos, full=full)
    out = dict(beta=beta, full_fit=full, n_jumps_matched=len(pos), exclusions=excl, n_intervals=len(S_rows),
               n_fires=len({r["fire_key"] for r in S_rows}), optimizer=dict(success=bool(res.success), fun=float(res.fun),
                                                                         message=str(res.message)), prior=PRIOR)
    (OUT / "fit_beta.json").write_text(json.dumps(out, indent=1))
    df, rng = gate_ranges(beta, keys)
    df.to_csv(OUT / "gate_training_fires.csv", index=False)
    (OUT / "gate_ranges.json").write_text(json.dumps(rng, indent=1))
    (OUT / "active_segment_test.json").write_text(json.dumps(active_segment_test(keys), indent=1))
    diag = training_diagnostic(beta, [p["fire_key"] for p in pos])
    (OUT / "training_diagnostic.json").write_text(json.dumps(diag, indent=1, default=float))
    print(json.dumps(out, indent=1), json.dumps(rng, indent=1), json.dumps({k: diag[k] for k in ("S1", "S2", "S3", "region_auc_M8", "region_auc_B1", "K_total")}, default=float))


if __name__ == "__main__":
    main()

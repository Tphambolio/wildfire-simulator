"""v8 secondary (never deciding) outcomes, PREREG_LR8 section 6 as amended by addendum A1.

Run after ``v8_score.py score`` on the unsealed ``final/`` directory. Reported only; nothing here changes a model,
a setting or the decision. Writes ``final/secondary.json``:

- AUC (mean percentile rank, S3 weights) of M8, B1 (active segments) and B1all (whole perimeter), by region, by
  year, by gap class of the jump (unburned / later_detected / never_detected) and by distance band (1-2, 2-3,
  > 3 km);
- predicted vs observed jumps per fire: M8 expected detected spots (sum of the M8 score over the candidate cells
  of every valid interval; b0 is fitted to detected training jumps) against the observed count secondary
  (positives + never-reached clusters, addendum A1);
- Brier of P(any jump in the interval) for M8, P = 1 - exp(-sum lambda), against "any positive or never-reached
  cluster"; intervals whose only cluster is 375-540 m outside the final perimeter are left out (neither);
- tail ratio of the M8 landing field P(> 3 km) / P(> 1 km), pooled over intervals, against 0.20 and against the
  observed jumps' ratio.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from . import common as C
from .intervals import m8_score

FINAL = C.DATA / "final"


def _load_score_module():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("v8_score", Path(__file__).resolve().parents[1] / "v8_score.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    S = _load_score_module()
    beta = json.loads((C.DATA / "fit" / "fit_beta.json").read_text())["beta"]
    cands, jumps, gate, counts = S.load_dir(FINAL)
    J = pd.read_csv(FINAL / "jumps.csv")
    models = ["M8", "B1", "B1all"] + (["ELMFIRE"] if any("score_ELMFIRE" in c for c in cands.values()) else [])
    rows = []
    for j in J.itertuples():
        if S.final_class(j.out_final_m) != "positive":
            continue
        c = cands.get((j.fire_key, str(j.interval)))
        if c is None:
            continue
        pos = np.where(c["cell_id"] == j.cell_id)[0]
        if not len(pos):
            continue
        r = dict(fire_key=j.fire_key, region=j.region, year=j.year, gap=j.gap, dist_m=j.dist_m)
        for m in models:
            sc = c.get(f"score_{m}")
            r[m] = float(S.pct_rank(sc, sc[pos[:1]])[0]) if sc is not None else np.nan
        rows.append(r)
    R = pd.DataFrame(rows)
    out = {"n_ranked": int(len(R))}

    def auc_by(col):
        res = {}
        if not len(R) or col not in R:
            return res
        for g, d in R.groupby(col, dropna=False):
            K = d.groupby("fire_key").size()
            w = (K / (1 + 0.3 * (K - 1))).to_dict()
            res[str(g)] = {"n_jumps": int(len(d)), "n_fires": int(d.fire_key.nunique())}
            for m in models:
                per = d.groupby("fire_key")[m].mean()
                ww = np.array([w[f] for f in per.index])
                res[str(g)][m] = float(np.nansum(ww * per.values) / ww.sum()) if ww.sum() else None
        return res

    R["dband"] = pd.cut(R.dist_m, [1000, 2000, 3000, 15001], labels=["1-2 km", "2-3 km", "3-15 km"]) if len(R) else []
    R["all"] = "all"
    out["auc"] = {k: auc_by(k) for k in ("all", "region", "year", "gap", "dband")}
    # predicted vs observed per fire, Brier, tail ratio
    obs_int = {}
    for j in J.itertuples():
        cls = S.final_class(j.out_final_m)
        key = (j.fire_key, str(j.interval))
        cur = obs_int.get(key, "none")
        if cls in ("positive", "never_reached_gt540m"):
            obs_int[key] = "jump"
        elif cur == "none":
            obs_int[key] = "neither"
    pred_fire, brier, far, near = {}, [], 0.0, 0.0
    el = pd.read_csv(FINAL / "counts.csv")
    for f in el.fire_key:
        fd = C.FIRES / f.replace(":", "_")
        done = fd / "intervals" / "_done.json"
        if not done.exists():
            continue
        info = json.loads(done.read_text())
        if info.get("status") != "ok":
            continue
        tot = 0.0
        for it in info["intervals"]:
            z = np.load(fd / "intervals" / f"{it['epoch']:05d}.npz")
            lam = m8_score(z, beta)
            s = float(lam.sum())
            tot += s
            far += float(lam[z["d_all"] > 3000].sum()); near += s
            o = obs_int.get((f, str(it["epoch"])), "none")
            if o != "neither":
                brier.append(((1 - np.exp(-s)) - (o == "jump")) ** 2)
        pred_fire[f] = tot
    acc = json.loads((FINAL / "score_out.json").read_text())["jump_accounting"]["per_fire"] if (FINAL / "score_out.json").exists() else {}
    obs_fire = {f: v["ranked"] + v["interval_excluded"] + v["not_candidate"] + v["never_reached_gt540m"] for f, v in acc.items()}
    P = pd.DataFrame({"pred": pd.Series(pred_fire), "obs": pd.Series(obs_fire)}).fillna(0.0)
    P["region"] = el.set_index("fire_key").region.reindex(P.index).values
    out["pred_vs_obs"] = {r: {"n_fires": int(len(d)), "pred_total": float(d.pred.sum()), "obs_total": float(d.obs.sum()),
                              "spearman": float(d.pred.rank().corr(d.obs.rank())) if len(d) > 2 else None}
                          for r, d in P.groupby("region")}
    out["brier_any_jump_M8"] = {"n_intervals": len(brier), "brier": float(np.mean(brier)) if brier else None,
                                "obs_rate": None}
    obs_any = [v == "jump" for v in obs_int.values()]
    out["brier_any_jump_M8"]["obs_rate_among_intervals_with_clusters"] = float(np.mean(obs_any)) if obs_any else None
    out["tail_ratio"] = {"M8_pred_gt3km_over_gt1km": far / near if near else None, "reference": 0.20,
                         "observed_jumps": float((R.dist_m > 3000).mean()) if len(R) else None}
    (FINAL / "secondary.json").write_text(json.dumps(out, indent=1, default=float))
    R.drop(columns=["all"]).to_csv(FINAL / "ranks_per_jump.csv", index=False)
    print(json.dumps(out["auc"]["all"], indent=1), json.dumps(out["pred_vs_obs"]), out["brier_any_jump_M8"], out["tail_ratio"])


if __name__ == "__main__":
    C.guard()
    main()

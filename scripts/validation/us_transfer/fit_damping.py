"""Size the candidate suppression-persistence damping rule on the R71 development fires.

Purpose: cause (c) (suppression / held fire) dominates the US over-prediction ([R71] §5). The candidate fix is a
parametric, outcome-free rule: when the fire barely grew on the previous day (share s of the start area that
burned on day - 1, ``start_prev_day_share`` of ``firesim.validation.start_strata``, known at forecast time),
every cell's rate of spread is multiplied by k_min for the burn day; otherwise k = 1:

    k(s) = k_min if s < s_ref else 1.

For each development fire-day that has the paired dose runs (``base`` k = 1 and ``rm050`` / ``rm030`` / ``rm020`` / ``rm010``),
L(k) = log10(pred + 10 ha) - log10(obs + 10 ha) and the 17 h F1 are interpolated piecewise-linearly in log k.
The pair (s_ref, k_min) minimising the mean of L^2 over US development fire-days is reported (grid s_ref in
{0.01, 0.02, 0.05, 0.10, 0.20}, k_min in [0.1, 1] by 0.05), with the US and Canadian mean L, mean |L| and F1 after
the rule, fire-bootstrap CIs (2,000 draws, seed 1). No ML; two parameters; development fires only.

Usage: ``python3 -m scripts.validation.us_transfer.fit_damping <out_dir>`` (writes ``damping_fit.json``).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from . import common as C
from .analyse import L, boot, read

KS = {"base": 1.0, "rm050": 0.5, "rm030": 0.3, "rm020": 0.2, "rm010": 0.1}
S_REF = (0.01, 0.02, 0.05, 0.10, 0.20)
K_MIN = tuple(np.round(np.arange(0.1, 1.0001, 0.05), 2))


def curves(region):
    man = {m["fire_id"]: m for m in json.loads((C.DATA / "manifest.json").read_text())}
    runs = {v: read(v) for v in KS}
    out = []
    for key, b in runs["base"].items():
        if "error" in b or man[key[0]]["region"] != region:
            continue
        if not all(key in runs[v] and "error" not in runs[v][key] for v in KS):
            continue
        ks, ls, fs = [], [], []
        for v, k in sorted(KS.items(), key=lambda x: x[1]):
            h = runs[v][key]["17h"]
            ks.append(math.log(k)); ls.append(L(h["pred_ha"], h["obs_ha"]))
            fs.append(h["f1"] if h["f1"] == h["f1"] else 0.0)
        out.append({"fire_id": key[0], "day": key[1], "s": b.get("start_prev_day_share"), "lk": ks, "L": ls, "f1": fs})
    return out


def at(c, k):
    return float(np.interp(math.log(k), c["lk"], c["L"])), float(np.interp(math.log(k), c["lk"], c["f1"]))


def apply(cs, s_ref, k_min):
    L_, F_, fz = [], [], []
    for c in cs:
        k = k_min if (c["s"] is not None and c["s"] < s_ref) else 1.0
        a, f = at(c, k)
        L_.append(a); F_.append(f); fz.append(c["fire_id"])
    return np.array(L_), np.array(F_), fz


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    us, ca = curves("US"), curves("CAN")
    grid = []
    for s_ref in S_REF:
        for k_min in K_MIN:
            Lu, _, _ = apply(us, s_ref, k_min)
            grid.append({"s_ref": s_ref, "k_min": float(k_min), "mse_US": float(np.mean(Lu ** 2)),
                         "bias_US": float(np.mean(Lu))})
    best = min(grid, key=lambda g: g["mse_US"])
    res = {"_about": {"purpose": "fit of the candidate damping rule on development fires only",
                      "created_by": "scripts/validation/us_transfer/fit_damping.py"},
           "n_US": len(us), "n_CAN": len(ca), "best": best, "grid": grid}
    for name, cs in (("US", us), ("CAN", ca)):
        L0, F0, fz = apply(cs, best["s_ref"], 1.0)
        L1, F1, _ = apply(cs, best["s_ref"], best["k_min"])
        res[name] = {"mean_L_base": boot(L0, fz), "mean_L_rule": boot(L1, fz), "dL": boot(L1 - L0, fz),
                     "mean_absL_base": boot(np.abs(L0), fz), "mean_absL_rule": boot(np.abs(L1), fz),
                     "d_absL": boot(np.abs(L1) - np.abs(L0), fz), "F1_base": boot(F0, fz), "F1_rule": boot(F1, fz),
                     "dF1": boot(F1 - F0, fz),
                     "share_days_damped": float(np.mean([c["s"] is not None and c["s"] < best["s_ref"] for c in cs]))}
    # a single global multiplier on every day (for comparison: is the persistence condition needed?)
    gl = []
    for k in K_MIN:
        Lu = np.array([at(c, k)[0] for c in us])
        gl.append({"k": float(k), "mse_US": float(np.mean(Lu ** 2)), "bias_US": float(np.mean(Lu))})
    res["global_multiplier"] = min(gl, key=lambda g: g["mse_US"]) | {"grid": gl}
    (out / "damping_fit.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({k: res[k] for k in ("n_US", "n_CAN", "best")}, default=float))
    for name in ("US", "CAN"):
        print(name, {k: [round(x, 3) for x in v] if isinstance(v, list) else v for k, v in res[name].items()})
    print("global", {k: v for k, v in res["global_multiplier"].items() if k != "grid"})


if __name__ == "__main__":
    main()

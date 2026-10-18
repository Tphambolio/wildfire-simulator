#!/usr/bin/env python3
"""v8_gate.py -- PREREG_LR8 kernel bound (design section 4.1) and the gate G3 check.

Purpose
    The v8 short-range ember kernel evaluates the Sardoy et al. (2008) correlation
    (``firesim.structures.embers.sardoy_lognormal``) only inside the domain Sardoy computed
    (10-40 MW/m, 6.70-17.88 m/s; Combust. Flame 154:478-488 p.483): I_eff = min(I, 40 MW/m),
    U_eff = clip(U, 6.70, 17.88 m/s). Below 10 MW/m the correlation is kept (it shortens distances; flagged
    as extrapolation). G3 (must pass before any decision run): over the whole input range the clamped
    short-mode median is <= 138 m and its 99.9th percentile <= 4.2 km, and the Sardoy Fig. 9 case
    (30 MW/m, 11.17 m/s) gives mu ~ 4.15, sigma ~ 1.08 (Sardoy's fit 4.20, 0.96; p.487).
Usage
    PYTHONPATH=engine/src python3 scripts/validation/v8_gate.py g3
"""
import sys

import numpy as np
from scipy.special import ndtri

from firesim.structures.embers import sardoy_lognormal

I_MAX_MW_M = 40.0
U_LO, U_HI = 6.70, 17.88
MEDIAN_MAX_M, P999_MAX_M = 138.0, 4200.0


def sardoy_clamped(ib_mw_m, u_ms):
    ib = np.minimum(np.asarray(ib_mw_m, float), I_MAX_MW_M)
    u = np.clip(np.asarray(u_ms, float), U_LO, U_HI)
    return sardoy_lognormal(ib, u)


def g3():
    ib, u = np.meshgrid(np.logspace(-2, 3, 400), np.linspace(0.1, 40.0, 400))
    mu, sg = sardoy_clamped(ib, u)
    med = np.exp(mu)
    p999 = np.exp(mu + sg * float(ndtri(0.999)))
    m9, s9 = (float(v) for v in sardoy_clamped(30.0, 11.17))
    out = dict(median_max_m=float(med.max()), p999_max_m=float(p999.max()), fig9_mu=m9, fig9_sigma=s9)
    out["pass"] = bool(out["median_max_m"] <= MEDIAN_MAX_M and out["p999_max_m"] <= P999_MAX_M
                       and abs(m9 - 4.15) < 0.02 and abs(s9 - 1.08) < 0.02)
    return out


if __name__ == "__main__" and sys.argv[1:] == ["g3"]:
    r = g3()
    print(r)
    sys.exit(0 if r["pass"] else 1)

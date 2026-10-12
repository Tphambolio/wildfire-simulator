#!/usr/bin/env python3
"""O-1a (matted) vs O-1b (standing) head-fire rate of spread across ISI, from FireSim's FBP layer
(cffdrs-verified), for the Alberta grass decision ([R35]). Curing cancels in the ratio (both
types use the same curing factor, Wotton et al. 2009 eq 35), so the ratio holds at any curing.

    PYTHONPATH=engine/src python scripts/validation/grass_o1a_o1b.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from firesim.fbp.calculator import calculate_rsi, calculate_grass_curing_factor
from firesim.fbp.constants import FUEL_TYPES, FuelType


def ros(ft, isi, cure):
    return calculate_rsi(FUEL_TYPES[ft], isi, grass_cure=cure)


def main() -> None:
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    isi = np.round(np.arange(0.5, 80.01, 0.5), 2)
    a = np.array([ros(FuelType.O1a, i, 100.0) for i in isi])
    b = np.array([ros(FuelType.O1b, i, 100.0) for i in isi])
    ratio = b / a
    cross = float(isi[np.argmax(ratio >= 1.0)])
    pts = {f"ISI {k}": {"O1a": float(ros(FuelType.O1a, k, 95.0)), "O1b": float(ros(FuelType.O1b, k, 95.0)),
                        "O1b_over_O1a": float(ros(FuelType.O1b, k, 95.0) / ros(FuelType.O1a, k, 95.0))}
           for k in (5, 10, 15, 20, 30, 40, 60)}
    res = {"crossover_isi": cross, "min_ratio": float(ratio.min()), "isi_at_min": float(isi[ratio.argmin()]),
           "max_ratio_to_isi80": float(ratio.max()), "at_95pct_curing_m_per_min": pts,
           "cf_95": calculate_grass_curing_factor(95.0)}
    (out / "grass_o1a_o1b.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.5, 3.8))
    cf = calculate_grass_curing_factor(95.0)
    ax1.plot(isi, a * cf, color="#a6761d", label="O-1a matted")
    ax1.plot(isi, b * cf, color="#1b7837", label="O-1b standing")
    ax1.set_xlabel("Initial Spread Index"); ax1.set_ylabel("head fire ROS, m/min (95 % cured, level)")
    ax1.legend(fontsize=8); ax1.set_title("FBP grass ROS (ST-X-3 eqs 26, 35; GLC-X-10 eq 35)", fontsize=9)
    ax2.plot(isi, ratio, color="k")
    ax2.axhline(1, color="grey", lw=0.6, ls=":")
    ax2.axvline(cross, color="#d95f02", lw=0.8, ls="--")
    ax2.text(cross + 1, 0.9, f"equal at ISI {cross:g}", fontsize=8, color="#d95f02")
    ax2.set_xlabel("Initial Spread Index"); ax2.set_ylabel("ROS O-1b / ROS O-1a (any curing)")
    ax2.set_title("Standing / matted ratio", fontsize=9)
    fig.tight_layout(); fig.savefig(out / "fig6_grass_o1a_o1b.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()

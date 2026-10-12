#!/usr/bin/env python3
"""Post-hoc figure for the void PREREG_105 test ([R35]): class shares inside 2025 perimeters vs
the 0.2-2 km ring, and per-fire SR(fuel) vs SR(105) with the gate. Descriptive only; not part
of the pre-registered rule.

    python scripts/validation/ab_fuel_105_posthoc_fig.py OUT_DIR   (reads OUT_DIR/burn_test_105.json)
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

out = Path(sys.argv[1])
r = json.loads((out / "burn_test_105.json").read_text())
F = r["fires"]
tb = sum(f["n_burned"] for f in F); ta = sum(f["n_avail"] for f in F)
labs = {"F": "fuel (FBP types)", "V": "105 vegetated non-fuel", "N": "101 non-fuel", "W": "102 water"}
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.5, 3.6))
x = np.arange(4); ks = "FVNW"
a1.bar(x - 0.2, [sum(f["b_" + k] for f in F) / tb for k in ks], 0.4, label="inside 2025 perimeters", color="#d95f02")
a1.bar(x + 0.2, [sum(f["a_" + k] for f in F) / ta for k in ks], 0.4, label="ring 0.2-2 km outside", color="#7570b3")
a1.set_xticks(x); a1.set_xticklabels([labs[k] for k in ks], fontsize=7); a1.set_ylabel("share of sample cells")
a1.legend(fontsize=7); a1.set_title("Alberta grid classes in and around 29 fires of 2025 (>= 1,000 ha)", fontsize=8)
a2.scatter([f["SR_F"] for f in F], [f["SR_V"] for f in F],
           c=["#1b9e77" if f["gate_pass"] else "#d95f02" for f in F], s=18)
a2.axvline(0.5, ls="--", color="grey", lw=0.8); a2.set_yscale("log")
a2.set_xlabel("SR fuel (gate: >= 0.5 to keep)"); a2.set_ylabel("SR 105 (log)")
a2.set_title(f"Gate (PREREG_105): {r['n_fires'] - r['n_kept']} of {r['n_fires']} fires fail -> test void", fontsize=8)
fig.tight_layout(); fig.savefig(out / "fig5_class105_2025_burns_void_test.png", dpi=150)

"""Quick look at one or more R71 run files (development use): n, mean log-area bias, median growth ratio.

Usage: python3 -m scripts.validation.us_transfer.quick [variant ...]
"""
import json
import sys

import numpy as np

from . import common as C


def main():
    man = {m["fire_id"]: m for m in json.loads((C.DATA / "manifest.json").read_text())}
    for v in sys.argv[1:] or ["base"]:
        rs = [json.loads(x) for x in (C.DATA / "runs" / f"{v}.jsonl").read_text().splitlines()]
        for reg in ("US", "CAN"):
            ok = [r for r in rs if "error" not in r and not r.get("same_as_base") and man[r["fire_id"]]["region"] == reg]
            if not ok:
                continue
            L = np.array([np.log10(r["17h"]["pred_ha"] + 10) - np.log10(r["17h"]["obs_ha"] + 10) for r in ok])
            g = np.array([r["17h"]["growth_ratio"] for r in ok], float)
            print(v, reg, "n", len(ok), "fires", len({r['fire_id'] for r in ok}), "errors",
                  sum("error" in r for r in rs), "mean L %.3f" % L.mean(), "median g %.2f" % np.nanmedian(g),
                  "g>=20 %.2f" % np.mean(g >= 20), "F1 %.3f" % np.nanmean([r["17h"]["f1"] for r in ok]),
                  "run_s med %.0f max %.0f" % (np.median([r["run_s"] for r in ok]), max(r["run_s"] for r in ok)))


if __name__ == "__main__":
    main()

"""Score PREREG_SEALED_CONFIRM_H1H2 (docs/PREREG_SEALED_CONFIRM_H1H2.md): H1 and H2 on the sealed 2026 confirm subset.

Purpose: apply the two registered decision rules to the run files, with the frozen R63 and B2 scorers, and
write one result file. Nothing here fits or tunes anything.

H1 (1- vs 2-burn-day active-edge rule, H = 24 h, mature fire-days): the frozen [R63] scorer
    ~/dev/wildfire/reports/data/active-rule-1v2-2026-10-24/scripts/score_1v2.py
    (sha256 32bf05ffc3c5f8572ae45007b99d6379233771c96837e6c75428e1cc1128e127), run unchanged on A and B.
    PASS = D = mean |ln gc_A| - |ln gc_B| at 17 h has 95 % CI upper < 0, AND paired dF1 (17 h) lower >= -0.01,
    AND rise in share g < 0.5 (point) <= +0.10.
H2 (B2 setting P2 vs today's raw ensemble): the frozen B2 scorer ``scripts/validation/ens_postprocess.py score``
    (sha256 60a1ebf7...; unchanged on master) with the frozen B2 fit
    ~/dev/wildfire/reports/data/b2-ensemble-calibration-2026-10-24/fit.json
    (sha256 a7c393ead090c8202006b6347ed2fa2f7b77a7918935b511fc4527016335fdd3; P2 shift -0.26, scale 1.55),
    copied into the output folder and re-hashed. The decision is B2 section 6 with P2 in place of P1:
    PASS = P2 coverage >= 0.70 AND lower 95 % CI of coverage(P2) - coverage(B) > 0 AND mean dCRPS(P2 - B) <= 0
    AND its upper 95 % CI <= +0.050.
Minimum data (both): >= 8 fires and >= 30 fire-days scored, else INCONCLUSIVE (the subset is still spent).

    PYTHONPATH=engine/src python3 scripts/validation/sealed2026/score_confirm.py RUNS_DIR OUT_DIR
RUNS_DIR holds A_1d_h24.jsonl, B_2d_h24.jsonl (H1) and ens20_A15.jsonl (H2). Writes OUT_DIR/h1/result_h1.json,
OUT_DIR/h2/{fit.json,results.json,per_fireday.csv} and OUT_DIR/result_confirm_h1h2.json.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home() / "dev/wildfire"
REPO = Path(__file__).resolve().parents[3]
R63_SCORER = HOME / "reports/data/active-rule-1v2-2026-10-24/scripts/score_1v2.py"
R63_SHA = "32bf05ffc3c5f8572ae45007b99d6379233771c96837e6c75428e1cc1128e127"
B2_FIT = HOME / "reports/data/b2-ensemble-calibration-2026-10-24/fit.json"
B2_FIT_SHA = "a7c393ead090c8202006b6347ed2fa2f7b77a7918935b511fc4527016335fdd3"
B2_SCORER = REPO / "scripts/validation/ens_postprocess.py"
B2_SCORER_SHA = "60a1ebf770c316ddc4f1e3166d0bc781bf8a28c6cafd45b1ea07a52d51ebe0fe"
MIN_FIRES, MIN_DAYS = 8, 30
NI_MARGIN = 0.050


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check(p: Path, want: str) -> None:
    if sha256(p) != want:
        raise SystemExit(f"frozen file changed: {p}")


def h1(runs: Path, out: Path) -> dict:
    check(R63_SCORER, R63_SHA)
    out.mkdir(parents=True, exist_ok=True)
    res_p = out / "result_h1.json"
    subprocess.run([sys.executable, str(R63_SCORER), str(runs / "A_1d_h24.jsonl"), str(runs / "B_2d_h24.jsonl"),
                    str(res_p), "PREREG_SEALED_CONFIRM_H1H2 H1 (sealed 2026 confirm)"], check=True)
    r = json.loads(res_p.read_text())
    w = r["windows"]["17h"]
    n_days = int(w["d_mean_abs_ln_g"][3])
    enough = r["n_fires"] >= MIN_FIRES and n_days >= MIN_DAYS
    dec = r["decision"]
    return {"scorer_sha256": R63_SHA, "n_fires": r["n_fires"], "n_fire_days_scored": n_days,
            "primary_D_17h": w["d_mean_abs_ln_g"], "guard1_dF1_17h": w["dF1"],
            "guard2_d_share_g_lt_0_5_17h": w["d_share_g_lt_0_5"], "r63_scorer_decision": dec,
            "minimum_data_met": enough,
            "verdict": (dec.get("verdict") if enough else "INCONCLUSIVE (minimum data not met)")}


def h2(runs: Path, out: Path) -> dict:
    check(B2_FIT, B2_FIT_SHA)
    check(B2_SCORER, B2_SCORER_SHA)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(B2_FIT, out / "fit.json")
    check(out / "fit.json", B2_FIT_SHA)
    subprocess.run([sys.executable, str(B2_SCORER), "score", "--test", str(runs / "ens20_A15.jsonl"),
                    "--out", str(out)], check=True)
    r = json.loads((out / "results.json").read_text())
    p2 = r["arms"]["P2"]
    c1 = p2["coverage"][0] >= 0.70
    c2 = p2["d_coverage_vs_B"][1] > 0.0
    c3 = p2["d_crps_vs_B"][0] <= 0.0 and p2["d_crps_vs_B"][2] <= NI_MARGIN
    enough = r["n_fires"] >= MIN_FIRES and r["n_fire_days"] >= MIN_DAYS
    verdict = ("PASS" if (c1 and c2 and c3) else "FAIL") if enough else "INCONCLUSIVE (minimum data not met)"
    return {"fit_sha256": B2_FIT_SHA, "scorer_sha256": B2_SCORER_SHA, "n_fires": r["n_fires"],
            "n_fire_days": r["n_fire_days"], "P2_shift_scale": [p2["shift"], p2["scale"]],
            "B_coverage": r["arms"]["B"]["coverage"], "P2_coverage": p2["coverage"],
            "d_coverage_P2_minus_B": p2["d_coverage_vs_B"], "B_crps": r["arms"]["B"]["crps"],
            "P2_crps": p2["crps"], "d_crps_P2_minus_B": p2["d_crps_vs_B"],
            "conditions": {"P2_coverage_ge_0.70": c1, "d_coverage_ci_lo_gt_0": c2,
                           "crps_non_inferior": c3},
            "minimum_data_met": enough, "verdict": verdict,
            "note": "results.json 'decision' is B2's own P1 rule, reported there for reference only; "
                    "the H2 verdict is this block (B2 section 6 with P2)."}


def main() -> None:
    runs, out = Path(sys.argv[1]), Path(sys.argv[2])
    res = {"_about": {"purpose": "PREREG_SEALED_CONFIRM_H1H2 decisions on the sealed 2026 US confirm subset",
                      "prereg": "docs/PREREG_SEALED_CONFIRM_H1H2.md", "runs_dir": str(runs),
                      "created_utc": datetime.now(timezone.utc).isoformat(),
                      "value_format": "[point, CI low, CI high(, n)]; CIs 95 % percentile, 2,000 whole-fire "
                                      "bootstrap draws, seed 1",
                      "multiplicity": "two separate tests, each at its own 95 % level; no joint claim"},
           "run_sha256": {p.name: sha256(p) for p in sorted(runs.glob("*.jsonl"))},
           "H1": h1(runs, out / "h1"), "H2": h2(runs, out / "h2")}
    (out / "result_confirm_h1h2.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k]["verdict"] for k in ("H1", "H2")}))


if __name__ == "__main__":
    main()

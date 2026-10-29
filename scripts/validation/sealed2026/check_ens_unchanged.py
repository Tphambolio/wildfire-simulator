"""Integrity check for PREREG_SEALED_CONFIRM_H1H2 H2: the ensemble code reproduces the B2 calibration rows.

Compares ensemble run files (validate.py run --ensemble output) record by record on the fire-days both hold:
every key of the reference record (recursively; NaN == NaN), except wall-clock timings and the domains path,
must be identical in the candidate. Exit status 1 when any value differs or no fire-day is shared.

    python check_ens_unchanged.py REF.jsonl CANDIDATE.jsonl OUT.json
"""
import json
import math
import sys
from pathlib import Path

SKIP = {"wall_s", "run_s", "domains_dir"}


def load(p):
    recs = (json.loads(x) for x in Path(p).read_text().splitlines() if x.strip())
    return {(r["fire_id"], r["day"]): r for r in recs if "error" not in r}


def diff(a, b, path, out):
    if isinstance(a, dict):
        if not isinstance(b, dict):
            out.append(path)
            return
        for k, v in a.items():
            if k in SKIP:
                continue
            if k not in b:
                out.append(f"{path}/{k} missing")
            else:
                diff(v, b[k], f"{path}/{k}", out)
    elif isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            out.append(f"{path}: list differs")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


ref, cand, outp = sys.argv[1:4]
A, B = load(ref), load(cand)
common = sorted(set(A) & set(B))
mism = []
for k in common:
    diff(A[k], B[k], f"{k[0]}:{k[1]}", mism)
res = {"_about": {"purpose": "H2 integrity: ensemble rows identical to the B2 calibration run on shared fire-days",
                  "reference": ref, "candidate": cand, "skipped_keys": sorted(SKIP)},
       "n_ref": len(A), "n_candidate": len(B), "n_common_fire_days": len(common),
       "n_mismatches": len(mism), "mismatches_first_20": mism[:20]}
Path(outp).write_text(json.dumps(res, indent=1))
print(json.dumps({k: v for k, v in res.items() if k != "_about"}))
sys.exit(1 if (mism or not common) else 0)

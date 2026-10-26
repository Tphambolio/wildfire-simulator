"""ELMFIRE subset worker k of n (PREREG_LR8 section 5; resumable: finished intervals have result.json).
Usage: capjob python3 -m scripts.validation.v8.elmfire_subset K N"""
import sys, json, traceback
import pandas as pd
from scripts.validation.v8 import common as C, elmfire as E
C.guard()
k, n = int(sys.argv[1]), int(sys.argv[2])
c = pd.read_csv(C.DATA / "draw" / "counts.csv")
keys = E.subset(list(c[c.eligible.astype(bool)].fire_key))
for i, f in enumerate(keys):
    if i % n != k:
        continue
    done = C.FIRES / f.replace(":", "_") / "intervals" / "_done.json"
    if not done.exists() or json.loads(done.read_text()).get("status") != "ok":
        print(f, "no intervals", flush=True); continue
    try:
        r = E.run_fire(f, "decision")
        print(f, len(r), sum(x["status"] == "ok" for x in r), flush=True)
    except Exception as e:
        traceback.print_exc(); C.log_run("elmfire", f, f"error {e!r}"[:200], 0); print(f, "ERROR", repr(e), flush=True)

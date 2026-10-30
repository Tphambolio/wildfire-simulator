"""Run the PREREG_LR8S simulated-edge runner (``simedge.run_fire``, sha256 in PREREG_LR8S A1) on every drawn eligible
pool fire of ``heldout/state.json`` whose registered intervals ran. Model runs only: no sealed file is read and nothing
is scored. Prints progress (fire rank, intervals written / dropped), never an outcome.

Usage (``scripts/validation``, ``PYTHONPATH=<repo>/engine/src``, under ``capjob``): python3 -m v8.simedge_pool [--pool A|E]
"""
from __future__ import annotations

import json
import shutil
import sys

from . import common as C
from . import simedge as S

if __name__ == "__main__":
    C.guard()
    st = json.loads((C.DATA / "heldout" / "state.json").read_text())
    pool = sys.argv[sys.argv.index("--pool") + 1] if "--pool" in sys.argv else None
    for k, s in st.items():
        if not (s.get("done") and s.get("eligible") and s.get("intervals") == "ok"):
            continue
        if pool and s["pool"] != pool:
            continue
        if shutil.disk_usage(C.DATA).free / 1024 ** 3 < 20:
            print("paused: < 20 GB free", flush=True); break
        if (C.fire_dir(k) / "simedge" / "_done.json").exists():
            continue
        try:
            r = S.run_fire(k, "heldout")
            print(s["pool"], s["region"], s["rank"], r["status"], r.get("n_written"), r.get("n_dropped"), flush=True)
        except Exception as e:
            C.log_run("simedge", k, f"error {e!r}"[:200], 0)
            print(s["pool"], s["region"], s["rank"], "ERROR", repr(e)[:120], flush=True)

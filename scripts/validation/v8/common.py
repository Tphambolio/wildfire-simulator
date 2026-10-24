"""Shared helpers for the v8 long-range ember runners (PREREG_LR8).

- Paths: ``FIRESIM_V8_DATA`` (default ``~/dev/wildfire/validation-data/v8-pool``) holds raw downloads,
  per-fire inputs and run outputs, outside the repository. ``FIRESIM_V8_PREFREEZE`` points at the frozen
  pre-freeze data folder (split, exclusions, the frozen jump-rule script ``v8_viirs_jumps.py``).
- Compute guard (PREREG section 8a.4): every runner process calls ``guard()`` first: address-space limit
  5 GB (``resource.RLIMIT_AS``), single-threaded BLAS/FFT; ``peak_rss_mb()`` is logged for every run in
  ``runs.csv`` by ``log_run``.
- The jump rule is the addendum-A1 version ``scripts/validation/v8_rule/v8_viirs_jumps.py`` (the frozen
  pre-freeze script with the < 60 min epoch merge of PREREG_LR8 section 4), imported after checking its
  sha256 against the addendum's script table, so every run uses exactly the registered rule.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import os
import resource
import sys
import time
from pathlib import Path

DATA = Path(os.environ.get("FIRESIM_V8_DATA", Path.home() / "dev/wildfire/validation-data/v8-pool"))
PREFREEZE = Path(os.environ.get("FIRESIM_V8_PREFREEZE",
                                Path.home() / "dev/wildfire/reports/data/v8-prefreeze-checks-2026-10-18"))
FIRES = DATA / "fires"
RUNS_CSV = DATA / "runs.csv"
JUMPS_FROZEN_SHA256 = "d03339573088222fce1607a31b8f71507cd2428b38e1b636b8d0465b321c1581"  # section 11 (superseded)
JUMPS_A1_SHA256 = "d480ca5abbda67a81cd86be5a41d9dfb3fbe55adbdad1e9a927678ab1fa433e6"  # addendum A1 script table
RULE_DIR = Path(__file__).resolve().parents[1] / "v8_rule"
RULE_OUT = Path(os.environ.get("FIRESIM_V8_RULE_OUT", DATA / "rule_A1"))  # A1 training catalogue
SPLIT_SHA256 = "dee6eaac66cf6ddccc89e5992009904e74dc099aaac08402d3a6f7a72163b5c1"
ORDER_SHA256 = "73bae68c0da1528314dea91e5634e8bebfcf84e5e94b39e22ac49b61a8a0f6cb"
MEM_LIMIT_BYTES = 5 * 1024 ** 3      # resident cap, enforced by capjob (cgroup MemoryMax=5G)
AS_BACKSTOP_BYTES = 16 * 1024 ** 3   # address-space backstop (virtual size overstates use: GEOS, FFT, BLAS)
CELL_M = 125.0          # model grid (m)
CAND_F = 3              # 3 x 125 m = 375 m candidate cells
PAD_M = 16000.0         # run box = final perimeter bounds + 16 km (candidate band 15 km + 1 km)


def guard() -> None:
    """Single-threaded numerics and a 16 GB address-space backstop; the binding 5 GB per-process limit is the
    resident-memory cgroup cap of ``capjob`` (PREREG section 8a.4 as amended by addendum A2, A2.9)."""
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(v, "1")
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    lim = AS_BACKSTOP_BYTES if hard == resource.RLIM_INFINITY else min(AS_BACKSTOP_BYTES, hard)
    resource.setrlimit(resource.RLIMIT_AS, (lim, hard))


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def log_run(step: str, fire_key: str, status: str, seconds: float, extra: str = "") -> None:
    RUNS_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not RUNS_CSV.exists()
    with open(RUNS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "step", "fire_key", "status", "seconds", "peak_rss_mb", "extra"])
        w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), step, fire_key, status,
                    f"{seconds:.1f}", f"{peak_rss_mb():.0f}", extra])


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def frozen_jumps():
    """The registered jump-rule module (addendum-A1 v8_viirs_jumps.py), sha256-checked."""
    p = RULE_DIR / "v8_viirs_jumps.py"
    if sha256(p) != JUMPS_A1_SHA256:
        raise RuntimeError(f"{p} does not match the PREREG_LR8 addendum A1 sha256")
    spec = importlib.util.spec_from_file_location("v8_viirs_jumps", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["v8_viirs_jumps"] = m
    spec.loader.exec_module(m)
    return m


def split_table():
    import pandas as pd
    p = PREFREEZE / "results" / "split.csv"
    if sha256(p) != SPLIT_SHA256:
        raise RuntimeError("split.csv does not match the PREREG_LR8 sha256")
    return pd.read_csv(p)


def fire_dir(fire_key: str) -> Path:
    d = FIRES / fire_key.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d

"""Shared helpers for the [R72] community-arrival under-spread diagnosis (stage 1, exploratory).

The C1b fire-days (PREREG_COMPARATOR_C1B, already scored in [R66] §12) are rebuilt exactly as the
C1b runner built them (``run_c1.run_day``: ``validate._load_fire`` + ``harness.build_case`` with
start 06:00 local, 24 h, 20 km margin, FireSim operational options: active edges 2 d, FFMC spin-up,
burning period 10-20 h), so the baseline variant reproduces the stored C1b FireSim arrival grids.
Every other variant changes one factor (paired one-factor sensitivity). Scoring is the registered
C1b metric (``communities_c1b``: interval-censored arrival error, not reached in 24 h = 24 h).

Nothing here is a registered test; the C1b days are diagnosed, not tuned.

Environment: the C1b data root is set here (FIRESIM_VALIDATION_DATA=<validation-data>/c1b,
FIRESIM_VALIDATION_DOMAINS=<validation-data>/c1b/domains) unless already set.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VD = Path("/home/rpas/dev/wildfire/validation-data")
os.environ.setdefault("FIRESIM_VALIDATION_DATA", str(VD / "c1b"))
os.environ.setdefault("FIRESIM_VALIDATION_DOMAINS", str(VD / "c1b/domains"))
sys.path[:0] = [str(REPO / "scripts/validation/c1"), str(REPO / "scripts"), str(REPO / "engine/src")]

import communities_c1b as CB  # noqa: E402

C1B_SETS = REPO / "scripts/validation/c1/sets"
C1B_DATA = Path("/home/rpas/dev/wildfire/reports/data/c1-wise-comparator-2026-10-24/c1b")
OUT = Path("/home/rpas/dev/wildfire/reports/data/arrival-underspread-r72-2026-10-29")
BIG = VD / "arrival-underspread-r72"  # arrival grids of the sensitivity runs (large, not in reports)
BP = (10.0, 20.0)


def read_set(name: str) -> list[tuple[str, int]]:
    p = C1B_SETS / f"community_c1b_{name}.txt"
    return [(a, int(b)) for a, b in (x.split(":") for x in p.read_text().split() if x)]


def all_days() -> list[tuple[str, int, str]]:
    """(fire, day, population) for the 14 primary and 6 calibration C1b fire-days."""
    return [(f, d, "primary") for f, d in read_set("primary")] + \
           [(f, d, "calibration") for f, d in read_set("calibration")]


@lru_cache(maxsize=2)
def load_fire(fid: str):
    from validate import _load_fire

    return _load_fire(fid)


def load_case(fid: str, day: int):
    from firesim.validation.harness import build_case

    dom, groups, rec = load_fire(fid)
    return build_case(dom, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)


def opts_operational(**kw):
    """FireSim's C1/C1b operational options (run_c1.opts_operational), with overrides."""
    from firesim.validation.harness import RunOptions

    base = dict(ignition="active", active_days=2, ffmc_spinup=True, burning_period=BP,
                windows_h=(4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 17.0, 24.0), oracle_max_h=17)
    base.update(kw)
    return RunOptions(**base)


def run(case, opts, member=None, ros_multiplier: float = 1.0, hourly=None, spinup=None,
        config_edit=None, fuel_edit=None):
    """Arrival minutes (inf = unburned) of one FireSim run of ``case``.

    ``hourly`` / ``spinup`` replace the case's weather streams (e.g. a station wind), ``config_edit``
    maps the SimulationConfig to a new one, ``fuel_edit`` edits the FuelGrid's type list in place.
    """
    from firesim.validation.harness import Member, _run, _setup

    if hourly is not None or spinup is not None:
        case = replace(case, hourly=hourly if hourly is not None else case.hourly,
                       spinup=spinup if spinup is not None else case.spinup)
    setup = _setup(case, opts, member or Member())
    if fuel_edit is not None:
        fuel_edit(setup["fuel_grid"], case)
    cfg = setup["config"] if config_edit is None else config_edit(setup["config"])
    arr, run_s, _ = _run(setup, cfg, opts, ros_multiplier,
                         seed=f"{case.fire_id}:{case.day}:{(member or Member()).name}")
    return arr.astype(np.float32), run_s


def score(case, arrival: np.ndarray) -> dict:
    """Registered C1b metric on one arrival grid (start area = arrival <= 0 of this grid)."""
    reached, unreached = CB.observed_intervals(case)
    start = arrival <= 0
    return CB.model_errors(reached, unreached, arrival, start)


def score_with_start(case, arrival: np.ndarray, start: np.ndarray) -> dict:
    """C1b metric with a fixed start mask (the baseline's), so variants score the same communities."""
    reached, unreached = CB.observed_intervals(case)
    return CB.model_errors(reached, unreached, arrival, start)


def stored_baseline(fid: str, day: int) -> np.ndarray:
    return np.load(C1B_DATA / "arrivals" / f"{fid}_{day}_det.npz")["firesim"]


def boot_fire(per_day: dict, seed: int = 20261029, n: int = 2000) -> dict:
    """Mean of per-fire-day values and a 95 % percentile bootstrap over whole fires."""
    keys = list(per_day)
    if not keys:
        return {"mean": math.nan, "lo": math.nan, "hi": math.nan, "n_days": 0, "n_fires": 0}
    fires = sorted({k[0] for k in keys})
    by = {f: [per_day[k] for k in keys if k[0] == f] for f in fires}
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.choice(len(fires), len(fires))
        vals = [v for i in pick for v in by[fires[i]]]
        means.append(float(np.mean(vals)))
    return {"mean": float(np.mean([per_day[k] for k in keys])),
            "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5)),
            "n_days": len(keys), "n_fires": len(fires)}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def cell_xy(dom):
    """Cell-centre x, y (m) on a local equirectangular plane (for distances on one domain)."""
    rr, cc = np.mgrid[0:dom.rows, 0:dom.cols]
    return (cc + 0.5) * dom.dx, (rr + 0.5) * dom.dy


def bearing_deg(dx: float, dy_north: float) -> float:
    return math.degrees(math.atan2(dx, dy_north)) % 360.0


def angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)
